import pytest
from effect2.testing import const, perform_sequence, raise_

from parsec.backend.backend_api import execute_cmd
from parsec.backend.pubkey import EPubkeyGet, EPubkeyAdd, MockedPubkeyComponent
from parsec.exceptions import PubKeyError, PubKeyNotFound
from parsec.crypto import RSAPublicKey


ALICE_PRIVATE_RSA = b"""
-----BEGIN RSA PRIVATE KEY-----
MIICWgIBAAKBgGITzwWRxv+mTAwqQd9pmQ8qqUO04KJSq1cH87KtmkqI3qewvXtW
qFsHP6ZNOT6wba7lrohJh1rDLU98GjorL4D/eX/mG/U9gURDi4aTTXT02RbHESBp
yMpeBUCzPTq1OgAk9OaawpV48vNkQifuT743hK49SLhqGNmNAnH2E3lxAgMBAAEC
gYBY2S0QFJG8AwCdfKKUK+t2q+UO6wscwdtqSk/grBg8MWXTb+8XjteRLy3gD9Eu
E1IpwPStjj7KYEnp2blAvOKY0E537d2a4NLrDbSi84q8kXqvv0UeGMC0ZB2r4C89
/6BTZii4mjIlg3iPtkbRdLfexjqmtELliPkHKJIIMH3fYQJBAKd/k1hhnoxEx4sq
GRKueAX7orR9iZHraoR9nlV69/0B23Na0Q9/mP2bLphhDS4bOyR8EXF3y6CjSVO4
LBDPOmUCQQCV5hr3RxGPuYi2n2VplocLK/6UuXWdrz+7GIqZdQhvhvYSKbqZ5tvK
Ue8TYK3Dn4K/B+a7wGTSx3soSY3RBqwdAkAv94jqtooBAXFjmRq1DuGwVO+zYIAV
GaXXa2H8eMqr2exOjKNyHMhjWB1v5dswaPv25tDX/caCqkBFiWiVJ8NBAkBnEnqo
Xe3tbh5btO7+08q4G+BKU9xUORURiaaELr1GMv8xLhBpkxy+2egS4v+Y7C3zPXOi
1oB9jz1YTnt9p6DhAkBy0qgscOzo4hAn062MAYWA6hZOTkvzRbRpnyTRctKwZPSC
+tnlGk8FAkuOm/oKabDOY1WZMkj5zWAXrW4oR3Q2
-----END RSA PRIVATE KEY-----
"""
ALICE_PUBLIC_RSA = b"""
-----BEGIN PUBLIC KEY-----
MIGeMA0GCSqGSIb3DQEBAQUAA4GMADCBiAKBgGITzwWRxv+mTAwqQd9pmQ8qqUO0
4KJSq1cH87KtmkqI3qewvXtWqFsHP6ZNOT6wba7lrohJh1rDLU98GjorL4D/eX/m
G/U9gURDi4aTTXT02RbHESBpyMpeBUCzPTq1OgAk9OaawpV48vNkQifuT743hK49
SLhqGNmNAnH2E3lxAgMBAAE=
-----END PUBLIC KEY-----
"""
BOB_PRIVATE_RSA = b"""
-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQDCqVQVdVhJqW9rrbObvDZ4ET6FoIyVn6ldWhOJaycMeFYBN3t+
cGr9/xHPGrYXK63nc8x4IVxhfXZ7JwrQ+AJv935S3rAV6JhDKDfDFrkzUVZmcc/g
HhjiP7rTAt4RtACvhZwrDuj3Pc4miCpGN/T3tbOKG889JN85nABKR9WkdwIDAQAB
AoGBAJFU3Dr9FgJA5rfMwpiV51CzByu61trqjgbtNkLVZhzwRr23z5Jxmd+yLHik
J6ia6sYvdUuHFLKQegGt/2xOjXn8UBpa725gLojHn2umtJDL7amTlBwiJfNXuZrF
BSKK9+xZnNDWMq1IuCqPeintbve+MNSc62JYuGGtXSz9L5f5AkEA/xBkUksBfEUl
65oEPgxvMKHNjLq48otRmCaG+i3MuQqTYQ+c8Z/l26yQL4OV2b36a8/tTaLhwhAZ
Ibtv05NKfQJBAMNgMbOsUWpY8A1Cec79Oj6RVe79E5ciZ4mW3lx5tjJRyNxwlQag
u+T6SwBIa6xMfLBQeizzxqXqxAyW/riQ6QMCQQCadUu7Re6tWZaAGTGufYsr8R/v
s/dh8ZpEwDgG8otCFzRul6zb6Y+huttJ2q55QIGQnka/N/7srSD6+23Zux1lAkBx
P30PzL6UimD7DqFUnev5AH1zPjbwz/x8AHt71wEJQebQAGIhqWHAZGS9ET14bg2I
ld172QI4glCJi6yyhyzJAkBzfmHZEE8FyLCz4z6b+Z2ghMds2Xz7RwgVqCIXt9Ku
P7Bq0eXXgyaBo+jpr3h4K7QnPh+PaHSlGqSfczZ6GIpx
-----END RSA PRIVATE KEY-----
"""
BOB_PUBLIC_RSA = b"""
-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDCqVQVdVhJqW9rrbObvDZ4ET6F
oIyVn6ldWhOJaycMeFYBN3t+cGr9/xHPGrYXK63nc8x4IVxhfXZ7JwrQ+AJv935S
3rAV6JhDKDfDFrkzUVZmcc/gHhjiP7rTAt4RtACvhZwrDuj3Pc4miCpGN/T3tbOK
G889JN85nABKR9WkdwIDAQAB
-----END PUBLIC KEY-----
"""


@pytest.fixture
def component():
    return MockedPubkeyComponent()


@pytest.fixture
def pubkeys_loaded(component):
    for intent in (EPubkeyAdd('alice@test.com', ALICE_PUBLIC_RSA),
                   EPubkeyAdd('bob@test.com', BOB_PUBLIC_RSA)):
        eff = component.perform_pubkey_add(intent)
        perform_sequence([], eff)


class TestPubkeyComponent:
    def test_pubkey_get_raw_ok(self, component, pubkeys_loaded):
        intent = EPubkeyGet('alice@test.com', raw=True)
        eff = component.perform_pubkey_get(intent)
        sequence = [
        ]
        ret = perform_sequence(sequence, eff)
        assert ret == ALICE_PUBLIC_RSA

    def test_pubkey_get_cooked_ok(self, component, pubkeys_loaded):
        intent = EPubkeyGet('alice@test.com', raw=False)
        eff = component.perform_pubkey_get(intent)
        sequence = [
        ]
        ret = perform_sequence(sequence, eff)
        assert isinstance(ret, RSAPublicKey)

    def test_pubkey_get_missing(self, component, pubkeys_loaded):
        intent = EPubkeyGet('unknown@test.com', raw=False)
        with pytest.raises(PubKeyNotFound):
            eff = component.perform_pubkey_get(intent)
            sequence = [
            ]
            perform_sequence(sequence, eff)

    def test_pubkey_add_ok(self, component):
        intent = EPubkeyAdd('alice@test.com', ALICE_PUBLIC_RSA)
        eff = component.perform_pubkey_add(intent)
        sequence = [
        ]
        ret = perform_sequence(sequence, eff)
        assert ret is None
        # Make sure key is present
        intent = EPubkeyGet('alice@test.com', raw=True)
        eff = component.perform_pubkey_get(intent)
        sequence = [
        ]
        ret = perform_sequence(sequence, eff)
        assert ret == ALICE_PUBLIC_RSA

    def test_pubkey_add_duplicated(self, component, pubkeys_loaded):
        intent = EPubkeyAdd('alice@test.com', BOB_PUBLIC_RSA)
        with pytest.raises(PubKeyError):
            eff = component.perform_pubkey_add(intent)
            sequence = [
            ]
            ret = perform_sequence(sequence, eff)
        # Make sure key hasn't changed
        intent = EPubkeyGet('alice@test.com', raw=True)
        eff = component.perform_pubkey_get(intent)
        sequence = [
        ]
        ret = perform_sequence(sequence, eff)
        assert ret == ALICE_PUBLIC_RSA


class TestPubkeyAPI:

    def test_pubkey_get_ok(self):
        eff = execute_cmd('pubkey_get', {'id': 'alice@test.com'})
        sequence = [
            (EPubkeyGet('alice@test.com', raw=True), const(b"alice's raw key"))
        ]
        ret = perform_sequence(sequence, eff)
        assert ret == {
            'status': 'ok',
            'id': 'alice@test.com',
            'key': b"alice's raw key"
        }

    @pytest.mark.parametrize('bad_msg', [
        {'id': 42},
        {'id': None},
        {'id': 'alice@test.com', 'unknown': 'field'},
        {}
    ])
    def test_pubkey_get_bad_msg(self, bad_msg):
        eff = execute_cmd('pubkey_get', bad_msg)
        sequence = [
        ]
        ret = perform_sequence(sequence, eff)
        assert ret['status'] == 'bad_msg'

    def test_pubkey_get_not_found(self):
        eff = execute_cmd('pubkey_get', {'id': 'alice@test.com'})
        sequence = [
            (EPubkeyGet('alice@test.com', raw=True), lambda x: raise_(PubKeyNotFound()))
        ]
        ret = perform_sequence(sequence, eff)
        assert ret['status'] == 'pubkey_not_found'
