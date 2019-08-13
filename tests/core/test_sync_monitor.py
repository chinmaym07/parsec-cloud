# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest


@pytest.mark.trio
async def test_autosync_on_modification(mock_clock, running_backend, alice_core, alice2_user_fs):
    mock_clock.rate = 0  # Avoid potential concurrency with monitors
    wid = await alice_core.user_fs.workspace_create("w")
    workspace = alice_core.user_fs.get_workspace(wid)
    await alice_core.user_fs.sync()

    mock_clock.rate = 1
    mock_clock.autojump_threshold = 0.1
    with alice_core.event_bus.listen() as spy:
        await workspace.mkdir("/foo")
        foo_id = await workspace.path_id("/foo")
        await spy.wait_with_timeout(
            "fs.entry.synced",
            {"workspace_id": wid, "id": foo_id},
            timeout=60,  # autojump, so not *really* 60s
        )

    await alice2_user_fs.sync()
    workspace2 = alice2_user_fs.get_workspace(wid)
    path_info = await workspace.path_info("/foo")
    path_info2 = await workspace2.path_info("/foo")
    assert path_info == path_info2
