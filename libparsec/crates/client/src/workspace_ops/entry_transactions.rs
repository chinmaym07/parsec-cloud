// Parsec Cloud (https://parsec.cloud) Copyright (c) BUSL-1.1 2016-present Scille SAS

use std::sync::Arc;

use libparsec_platform_storage::workspace::GetChildManifestError as StorageGetChildManifestError;
use libparsec_types::prelude::*;

use super::{
    fetch::{fetch_remote_child_manifest, FetchRemoteManifestError},
    WorkspaceOps,
};
use crate::certificates_ops::{InvalidCertificateError, InvalidManifestError};

#[derive(Debug, thiserror::Error)]
pub enum GetChildManifestError {
    #[error("Cannot reach the server")]
    Offline,
    #[error("This manifest doesn't exist")]
    NotFound,
    #[error("Not allowed to access this realm")]
    NotAllowed,
    #[error(transparent)]
    InvalidCertificate(#[from] InvalidCertificateError),
    #[error(transparent)]
    InvalidManifest(#[from] InvalidManifestError),
    #[error("Our clock ({client_timestamp}) and the server's one ({server_timestamp}) are too far apart")]
    BadTimestamp {
        server_timestamp: DateTime,
        client_timestamp: DateTime,
        ballpark_client_early_offset: f64,
        ballpark_client_late_offset: f64,
    },
    #[error(transparent)]
    Internal(#[from] anyhow::Error),
}

async fn get_child_manifest(
    ops: &WorkspaceOps,
    entry_id: VlobID,
) -> Result<ArcLocalChildManifest, GetChildManifestError> {
    match ops.data_storage.get_child_manifest(entry_id).await {
        Ok(manifest) => Ok(manifest),
        Err(StorageGetChildManifestError::Internal(err)) => Err(err.into()),
        Err(StorageGetChildManifestError::NotFound) => {
            let remote_manifest = fetch_remote_child_manifest(ops, entry_id, None)
                .await
                .map_err(|error| match error {
                    FetchRemoteManifestError::Offline => GetChildManifestError::Offline,
                    FetchRemoteManifestError::NotFound => GetChildManifestError::NotFound,
                    FetchRemoteManifestError::NotAllowed => GetChildManifestError::NotAllowed,
                    FetchRemoteManifestError::InvalidCertificate(x) => {
                        GetChildManifestError::InvalidCertificate(x)
                    }
                    FetchRemoteManifestError::InvalidManifest(x) => {
                        GetChildManifestError::InvalidManifest(x)
                    }
                    FetchRemoteManifestError::Internal(x) => GetChildManifestError::Internal(x),
                    FetchRemoteManifestError::BadVersion => {
                        // We provided `None` as version (hence `Internal` error is returned
                        // if the server responds with a `bad_version` status)
                        unreachable!()
                    }
                })?;

            // Must save our manifest in the storage
            let (updater, expect_missing_manifest) =
                ops.data_storage.for_update_child_manifest(entry_id).await?;
            match expect_missing_manifest {
                // Plot twist: a concurrent operation has inserted the manifest in the storage !
                // TODO: we could be trying to update the existing data with the brand new one
                // however this would most likely do nothing (as the concurrent version must based
                // on very recent data)
                Some(local_manifest) => Ok(local_manifest),

                // As expected the storage didn't contain the manifest, it's up to us to store it then !
                None => {
                    let local_manifest = match remote_manifest {
                        ChildManifest::File(remote_manifest) => {
                            let manifest =
                                Arc::new(LocalFileManifest::from_remote(remote_manifest));
                            updater
                                .set_file_manifest(manifest.clone(), false, [].into_iter())
                                .await?;
                            ArcLocalChildManifest::File(manifest)
                        }
                        ChildManifest::Folder(remote_manifest) => {
                            let manifest =
                                Arc::new(LocalFolderManifest::from_remote(remote_manifest, None));
                            updater.set_folder_manifest(manifest.clone()).await?;
                            ArcLocalChildManifest::Folder(manifest)
                        }
                    };
                    Ok(local_manifest)
                }
            }
        }
    }
}

#[derive(Debug, Clone)]
pub enum EntryInfo {
    File {
        /// The confinement point corresponds to the entry id of the parent folderish
        /// manifest that contains a child with a confined name in the path leading
        /// to our entry.
        confinement_point: Option<VlobID>,
        id: VlobID,
        created: DateTime,
        updated: DateTime,
        base_version: VersionInt,
        is_placeholder: bool,
        need_sync: bool,
        size: SizeInt,
    },
    // Here Folder can also be the root of the workspace (i.e. WorkspaceManifest)
    Folder {
        /// The confinement point corresponds to the entry id of the parent folderish
        /// manifest that contains a child with a confined name in the path leading
        /// to our entry.
        confinement_point: Option<VlobID>,
        id: VlobID,
        created: DateTime,
        updated: DateTime,
        base_version: VersionInt,
        is_placeholder: bool,
        need_sync: bool,
        children: Vec<EntryName>,
    },
}

struct FsPathResolution {
    entry_id: VlobID,
    /// The confinement point corresponds to the entry id of the folderish manifest
    /// (i.e. file or workspace manifest) that contains a child with a confined name
    /// in the corresponding path.
    ///
    /// If the entry is not confined, the confinement point is `None`.
    confinement_point: Option<VlobID>,
}

#[derive(Debug, thiserror::Error)]
pub enum EntryIDFromPathError {
    #[error("Path doesn't exist")]
    NotFound,
    #[error("Cannot reach the server")]
    Offline,
    #[error("Not allowed to access this realm")]
    NotAllowed,
    #[error(transparent)]
    InvalidCertificate(#[from] InvalidCertificateError),
    #[error(transparent)]
    InvalidManifest(#[from] InvalidManifestError),
    #[error("Our clock ({client_timestamp}) and the server's one ({server_timestamp}) are too far apart")]
    BadTimestamp {
        server_timestamp: DateTime,
        client_timestamp: DateTime,
        ballpark_client_early_offset: f64,
        ballpark_client_late_offset: f64,
    },
    #[error(transparent)]
    Internal(#[from] anyhow::Error),
}

async fn resolve_path(
    ops: &WorkspaceOps,
    path: &FsPath,
) -> Result<FsPathResolution, EntryIDFromPathError> {
    enum Parent {
        Root,
        /// The parent is itself the child of someone else
        Child(FsPathResolution),
    }
    let mut parent = Parent::Root;

    for child_name in path.parts() {
        let resolution = match parent {
            Parent::Root => {
                let manifest = ops.data_storage.get_workspace_manifest();

                let child_entry_id = manifest
                    .children
                    .get(child_name)
                    .ok_or(EntryIDFromPathError::NotFound)?;

                let confinement_point = manifest
                    .local_confinement_points
                    .contains(child_entry_id)
                    .then_some(ops.realm_id);

                FsPathResolution {
                    entry_id: *child_entry_id,
                    confinement_point,
                }
            }

            Parent::Child(parent) => {
                let manifest = get_child_manifest(ops, parent.entry_id)
                    .await
                    .map_err(|err| match err {
                        GetChildManifestError::Offline => EntryIDFromPathError::Offline,
                        GetChildManifestError::NotFound => EntryIDFromPathError::NotFound,
                        GetChildManifestError::NotAllowed => EntryIDFromPathError::NotAllowed,
                        GetChildManifestError::InvalidCertificate(x) => {
                            EntryIDFromPathError::InvalidCertificate(x)
                        }
                        GetChildManifestError::InvalidManifest(x) => {
                            EntryIDFromPathError::InvalidManifest(x)
                        }
                        GetChildManifestError::BadTimestamp {
                            server_timestamp,
                            client_timestamp,
                            ballpark_client_early_offset,
                            ballpark_client_late_offset,
                        } => EntryIDFromPathError::BadTimestamp {
                            server_timestamp,
                            client_timestamp,
                            ballpark_client_early_offset,
                            ballpark_client_late_offset,
                        },
                        GetChildManifestError::Internal(x) => x
                            .context(format!(
                                "Cannot get manifest (vlob id: {})",
                                parent.entry_id
                            ))
                            .into(),
                    })?;

                let (children, local_confinement_points) = match &manifest {
                    ArcLocalChildManifest::File(_) => {
                        // Cannot continue to resolve the path !
                        return Err(EntryIDFromPathError::NotFound);
                    }
                    ArcLocalChildManifest::Folder(manifest) => {
                        (&manifest.children, &manifest.local_confinement_points)
                    }
                };

                let child_entry_id = children
                    .get(child_name)
                    .ok_or(EntryIDFromPathError::NotFound)?;

                // Top-most confinement point shadows child ones if any
                let confinement_point = match parent.confinement_point {
                    confinement_point @ Some(_) => confinement_point,
                    None => local_confinement_points
                        .contains(child_entry_id)
                        .then_some(parent.entry_id),
                };

                FsPathResolution {
                    entry_id: *child_entry_id,
                    confinement_point,
                }
            }
        };

        parent = Parent::Child(resolution);
    }

    Ok(match parent {
        Parent::Root => FsPathResolution {
            entry_id: ops.realm_id,
            confinement_point: None,
        },
        Parent::Child(resolution) => resolution,
    })
}

#[derive(Debug, thiserror::Error)]
pub enum EntryInfoError {
    #[error("Cannot reach the server")]
    Offline,
    #[error("Path doesn't exist")]
    NotFound,
    #[error("Not allowed to access this realm")]
    NotAllowed,
    #[error(transparent)]
    InvalidCertificate(#[from] InvalidCertificateError),
    #[error(transparent)]
    InvalidManifest(#[from] InvalidManifestError),
    #[error("Our clock ({client_timestamp}) and the server's one ({server_timestamp}) are too far apart")]
    BadTimestamp {
        server_timestamp: DateTime,
        client_timestamp: DateTime,
        ballpark_client_early_offset: f64,
        ballpark_client_late_offset: f64,
    },
    #[error(transparent)]
    Internal(#[from] anyhow::Error),
}

pub(super) async fn entry_info(
    ops: &WorkspaceOps,
    path: &FsPath,
) -> Result<EntryInfo, EntryInfoError> {
    // Special case for /
    if path.is_root() {
        let manifest = ops.data_storage.get_workspace_manifest();
        // Ensure children are sorted alphabetically to simplify testing
        let children = {
            let mut children: Vec<_> = manifest
                .children
                .keys()
                .map(|name| name.to_owned())
                .collect();
            children.sort_unstable_by(|a, b| a.as_ref().cmp(b.as_ref()));
            children
        };

        let info = EntryInfo::Folder {
            // Root has no parent, hence confinement_point is never possible
            confinement_point: None,
            id: manifest.base.id,
            created: manifest.base.created,
            updated: manifest.updated,
            base_version: manifest.base.version,
            is_placeholder: manifest.base.version == 0,
            need_sync: manifest.need_sync,
            children,
        };
        return Ok(info);
    }

    let resolution = resolve_path(ops, path).await.map_err(|err| match err {
        EntryIDFromPathError::NotFound => EntryInfoError::NotFound,
        EntryIDFromPathError::Offline => EntryInfoError::Offline,
        EntryIDFromPathError::NotAllowed => EntryInfoError::NotAllowed,
        EntryIDFromPathError::InvalidCertificate(x) => EntryInfoError::InvalidCertificate(x),
        EntryIDFromPathError::InvalidManifest(x) => EntryInfoError::InvalidManifest(x),
        EntryIDFromPathError::BadTimestamp {
            server_timestamp,
            client_timestamp,
            ballpark_client_early_offset,
            ballpark_client_late_offset,
        } => EntryInfoError::BadTimestamp {
            server_timestamp,
            client_timestamp,
            ballpark_client_early_offset,
            ballpark_client_late_offset,
        },
        EntryIDFromPathError::Internal(x) => x.context("Cannot resolve path").into(),
    })?;

    let manifest = get_child_manifest(ops, resolution.entry_id)
        .await
        .map_err(|err| match err {
            GetChildManifestError::Offline => EntryInfoError::Offline,
            GetChildManifestError::NotFound => EntryInfoError::NotFound,
            GetChildManifestError::NotAllowed => EntryInfoError::NotAllowed,
            GetChildManifestError::InvalidCertificate(x) => EntryInfoError::InvalidCertificate(x),
            GetChildManifestError::InvalidManifest(x) => EntryInfoError::InvalidManifest(x),
            GetChildManifestError::BadTimestamp {
                server_timestamp,
                client_timestamp,
                ballpark_client_early_offset,
                ballpark_client_late_offset,
            } => EntryInfoError::BadTimestamp {
                server_timestamp,
                client_timestamp,
                ballpark_client_early_offset,
                ballpark_client_late_offset,
            },
            GetChildManifestError::Internal(x) => x
                .context(format!(
                    "Cannot get manifest (vlob id: {})",
                    resolution.entry_id
                ))
                .into(),
        })?;

    let info = match manifest {
        ArcLocalChildManifest::File(m) => EntryInfo::File {
            confinement_point: resolution.confinement_point,
            id: m.base.id,
            created: m.base.created,
            updated: m.updated,
            base_version: m.base.version,
            is_placeholder: m.base.version == 0,
            need_sync: m.need_sync,
            size: m.size,
        },
        ArcLocalChildManifest::Folder(m) => EntryInfo::Folder {
            confinement_point: resolution.confinement_point,
            id: m.base.id,
            created: m.base.created,
            updated: m.updated,
            base_version: m.base.version,
            is_placeholder: m.base.version == 0,
            need_sync: m.need_sync,
            children: m.children.keys().map(|name| name.to_owned()).collect(),
        },
    };

    Ok(info)
}
