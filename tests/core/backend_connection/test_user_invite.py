import pytest
import trio
import pendulum

from parsec.trustchain import certify_user, certify_device
from parsec.core.types import RemoteUser
from parsec.core.backend_connection2 import (
    BackendNotAvailable,
    backend_cmds_connect,
    backend_anonymous_cmds_connect,
)
from parsec.core.invite_claim import generate_user_encrypted_claim, extract_user_encrypted_claim


@pytest.mark.trio
async def test_user_invite_backend_offline(alice_core, mallory):
    with pytest.raises(BackendNotAvailable):
        await alice_core.user_invite(mallory.user_id)


@pytest.mark.trio
async def test_user_invite_then_claim_ok(alice, alice_core, mallory, running_backend):
    async def _alice_invite():
        encrypted_claim = await alice_core.user_invite(mallory.user_id)
        claim = extract_user_encrypted_claim(alice.private_key, encrypted_claim)

        now = pendulum.now()
        certified_user = certify_user(
            alice.device_id,
            alice.signing_key,
            claim["device_id"].user_id,
            claim["public_key"],
            now=now,
        )
        certified_device = certify_device(
            alice.device_id, alice.signing_key, claim["device_id"], claim["verify_key"], now=now
        )
        await alice_core.user_create(certified_user, certified_device)

    async def _mallory_claim():
        async with backend_anonymous_cmds_connect(mallory.backend_addr) as conn:
            invitation_creator = await conn.user_get_invitation_creator(mallory.user_id)
            assert isinstance(invitation_creator, RemoteUser)

            encrypted_claim = generate_user_encrypted_claim(
                invitation_creator.public_key,
                mallory.device_id,
                mallory.public_key,
                mallory.verify_key,
            )
            await conn.user_claim(mallory.user_id, encrypted_claim)

    async with trio.open_nursery() as nursery:
        nursery.start_soon(_alice_invite)
        await running_backend.backend.event_bus.spy.wait(
            "event.connected", kwargs={"event_name": "user.claimed"}
        )
        nursery.start_soon(_mallory_claim)

    # Now mallory should be able to connect to backend
    async with backend_cmds_connect(
        mallory.backend_addr, mallory.device_id, mallory.signing_key
    ) as conn:
        pong = await conn.ping("Hello World !")
        assert pong == "Hello World !"
