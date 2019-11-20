#! /usr/bin/env python3
# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS


import os
import trio

from tqdm import tqdm
from humanize import naturalsize

from parsec.logging import configure_logging
from parsec.core.types import EntryID, FsPath
from parsec.core.logged_core import logged_core_factory
from parsec.core.config import get_default_config_dir, load_config
from parsec.core.local_device import list_available_devices, load_device_with_password


LOG_LEVEL = "WARNING"
DEVICE_ID = "alice@laptop"
PASSWORD = "test"


async def make_workspace_dir_inconsistent(device, workspace, directory):
    directory = FsPath(directory)
    await workspace.mkdir(directory)
    await workspace.touch(directory / "foo.txt")
    rep_info = await workspace.transactions.entry_info(directory)
    rep_manifest = await workspace.local_storage.get_manifest(rep_info["id"])
    children = rep_manifest.children
    children["corrupted.txt"] = EntryID("b9295787-d9aa-6cbd-be27-1ff83ac72fa6")
    rep_manifest.evolve(children=children)
    async with workspace.local_storage.lock_manifest(rep_info["id"]):
        await workspace.local_storage.set_manifest(rep_info["id"], rep_manifest)
    await workspace.sync()
    corrupted_path = directory / "corrupted.txt"
    print(f"{device.device_id} | {workspace.get_workspace_name()} | {corrupted_path}")


async def benchmark_file_writing(device, workspace):
    # Touch file
    path = "/foo"
    block_size = 512 * 1024
    file_size = 64 * 1024 * 1024
    await workspace.touch(path)
    info = await workspace.path_info(path)

    # Log
    print(f"{device.device_id} | {workspace.get_workspace_name()} | {path}")

    # Write file
    start = info["size"] // block_size
    stop = file_size // block_size
    for i in tqdm(range(start, stop)):
        await workspace.write_bytes(path, b"\x00" * block_size, offset=i * block_size)

    # Sync
    await workspace.sync()

    # Serialize
    manifest = await workspace.local_storage.get_manifest(info["id"])
    raw = manifest.dump()

    # Printout
    print(f"File size: {naturalsize(file_size)}")
    print(f"Manifest size: {naturalsize(len(raw))}")

    # Let sync monitor finish
    await trio.sleep(2)


async def main():

    # Config
    configure_logging(LOG_LEVEL)
    config_dir = get_default_config_dir(os.environ)
    config = load_config(config_dir)
    devices = list_available_devices(config_dir)
    key_file = next(key_file for _, device_id, _, key_file in devices if device_id == DEVICE_ID)
    device = load_device_with_password(key_file, PASSWORD)

    # Log in
    async with logged_core_factory(config, device) as core:

        # Get workspace
        user_manifest = core.user_fs.get_user_manifest()
        workspace_entry = user_manifest.workspaces[0]
        workspace = core.user_fs.get_workspace(workspace_entry.id)

        await make_workspace_dir_inconsistent(device, workspace, "/bar")
        await benchmark_file_writing(device, workspace)


if __name__ == "__main__":
    trio.run(main)
