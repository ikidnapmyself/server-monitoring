"""Tests for the client address behind trusted proxies."""

import pytest
from django.test import RequestFactory, override_settings

from config.client_ip import client_ip


def _request(peer, forwarded=None):
    meta = {"REMOTE_ADDR": peer}
    if forwarded is not None:
        meta["HTTP_X_FORWARDED_FOR"] = forwarded
    return RequestFactory().get("/", **meta)


@override_settings(TRUSTED_PROXIES=())
def test_with_nothing_trusted_the_header_is_ignored():
    assert client_ip(_request("10.0.0.1", "1.2.3.4")) == "10.0.0.1"


@override_settings(TRUSTED_PROXIES=("10.0.0.0/8",))
def test_an_untrusted_peer_cant_pick_its_address():
    assert client_ip(_request("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


@override_settings(TRUSTED_PROXIES=("unix",))
def test_a_unix_socket_peer_is_trusted_when_listed():
    assert client_ip(_request("", "198.51.100.7")) == "198.51.100.7"


@override_settings(TRUSTED_PROXIES=("10.0.0.1",))
def test_a_unix_socket_peer_isnt_trusted_unless_listed():
    assert client_ip(_request("", "198.51.100.7")) == ""


@override_settings(TRUSTED_PROXIES=("10.0.0.0/8", "172.16.0.0/12"))
def test_trusted_hops_are_skipped_from_the_right():
    forwarded = "6.6.6.6, 198.51.100.7, 172.16.0.5"
    assert client_ip(_request("10.0.0.1", forwarded)) == "198.51.100.7"


@override_settings(TRUSTED_PROXIES=("::1",))
def test_ipv6_works_and_comes_back_canonical():
    assert client_ip(_request("::1", "2001:DB8::0001")) == "2001:db8::1"


@pytest.mark.parametrize("forwarded", [None, "", "   ", "10.0.0.2", "not-an-ip", "1.2.3.4, junk"])
@override_settings(TRUSTED_PROXIES=("10.0.0.0/8",))
def test_a_header_with_no_usable_client_falls_back_to_the_peer(forwarded):
    assert client_ip(_request("10.0.0.1", forwarded)) == "10.0.0.1"


@override_settings(TRUSTED_PROXIES=("bogus", "10.0.0.0/8"))
def test_a_bad_entry_is_skipped_not_raised():
    assert client_ip(_request("10.0.0.1", "1.2.3.4")) == "1.2.3.4"


@override_settings(TRUSTED_PROXIES=("10.0.0.0/8",))
def test_a_request_without_a_peer_address_is_unknown():
    request = RequestFactory().get("/")
    del request.META["REMOTE_ADDR"]
    assert client_ip(request) == "unknown"


@override_settings(TRUSTED_PROXIES=("::1", "10.0.0.0/8"))
def test_mixed_family_entries_trust_a_v4_peer():
    assert client_ip(_request("10.0.0.1", "1.2.3.4")) == "1.2.3.4"


@override_settings(TRUSTED_PROXIES=("10.0.0.0/8",))
def test_an_ipv4_mapped_peer_isnt_trusted_by_a_v4_range():
    assert client_ip(_request("::ffff:10.0.0.1", "1.2.3.4")) == "::ffff:10.0.0.1"


@pytest.mark.parametrize("hop", ["1.2.3.4:5678", "fe80::1%eth0"])
@override_settings(TRUSTED_PROXIES=("10.0.0.0/8",))
def test_a_hop_that_isnt_a_bare_ip_falls_back_to_the_peer(hop):
    assert client_ip(_request("10.0.0.1", hop)) == "10.0.0.1"
