"""Tests for the listening-port audit checker."""

import sys
from collections import namedtuple
from unittest import mock

import psutil
from django.test import TestCase, override_settings

from apps.checkers.checkers.base import CheckStatus
from apps.checkers.checkers.listening_ports import (
    ListeningPort,
    ListeningPortsChecker,
    flagged_ports,
)

addr = namedtuple("addr", ["ip", "port"])
sconn = namedtuple("sconn", ["fd", "family", "type", "laddr", "raddr", "status", "pid"])


def _conn(ip, port, status="LISTEN", pid=100):
    return sconn(1, 2, 1, addr(ip, port), (), status, pid)


def _lp(ip, port, exposed):
    return ListeningPort(ip=ip, port=port, family="ipv4", pid=1, exposed=exposed)


class FlaggedPortsTests(TestCase):
    def test_with_allowlist_flags_any_not_allowed_incl_loopback(self):
        ports = [
            _lp("0.0.0.0", 22, True),
            _lp("127.0.0.1", 5432, False),
            _lp("0.0.0.0", 8080, True),
        ]
        flagged = flagged_ports(ports, {22})
        # 5432 (loopback) and 8080 both flagged because a policy exists.
        self.assertEqual({p.port for p in flagged}, {5432, 8080})

    def test_no_allowlist_flags_only_exposed(self):
        ports = [_lp("0.0.0.0", 8080, True), _lp("127.0.0.1", 5432, False)]
        flagged = flagged_ports(ports, set())
        self.assertEqual({p.port for p in flagged}, {8080})  # loopback not flagged

    def test_no_ports_no_flags(self):
        self.assertEqual(flagged_ports([], set()), [])


class ListeningPortsCheckerTests(TestCase):
    def _start(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch_conns(self, conns):
        self._start(mock.patch.object(sys, "platform", "linux"))
        self._start(
            mock.patch(
                "apps.checkers.checkers.listening_ports.psutil.net_connections",
                return_value=conns,
            )
        )

    @override_settings(LISTENING_PORTS_ALLOWLIST=[22, 443])
    def test_all_allowed_is_ok(self):
        self._patch_conns([_conn("0.0.0.0", 22), _conn("0.0.0.0", 443)])
        result = ListeningPortsChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["listening_count"], 2)
        self.assertEqual(result.metrics["unexpected_ports"], [])

    @override_settings(LISTENING_PORTS_ALLOWLIST=[22])
    def test_unexpected_port_warns_regardless_of_exposure(self):
        self._patch_conns([_conn("0.0.0.0", 22), _conn("127.0.0.1", 6379)])
        result = ListeningPortsChecker().check()
        self.assertEqual(result.status, CheckStatus.WARNING)
        self.assertEqual(result.metrics["unexpected_ports"], [6379])
        self.assertIn("6379", result.message)

    @override_settings(LISTENING_PORTS_ALLOWLIST=[])
    def test_no_allowlist_warns_on_exposed_only(self):
        self._patch_conns([_conn("0.0.0.0", 8080), _conn("127.0.0.1", 5432)])
        result = ListeningPortsChecker().check()
        self.assertEqual(result.status, CheckStatus.WARNING)
        self.assertEqual(result.metrics["unexpected_ports"], [8080])  # loopback 5432 not flagged

    @override_settings(LISTENING_PORTS_ALLOWLIST=[])
    def test_no_allowlist_loopback_only_is_ok(self):
        self._patch_conns([_conn("127.0.0.1", 5432), _conn("::1", 6379)])
        result = ListeningPortsChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertEqual(result.metrics["unexpected_ports"], [])

    @override_settings(LISTENING_PORTS_ALLOWLIST=[22])
    def test_non_listen_sockets_ignored(self):
        self._patch_conns([_conn("0.0.0.0", 22), _conn("1.2.3.4", 55000, status="ESTABLISHED")])
        result = ListeningPortsChecker().check()
        self.assertEqual(result.metrics["listening_count"], 1)

    def test_non_linux_skips_ok(self):
        self._start(mock.patch.object(sys, "platform", "darwin"))
        result = ListeningPortsChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("not Linux", result.message)
        self.assertEqual(result.metrics["listening_count"], 0)

    def test_access_denied_skips_ok(self):
        self._start(mock.patch.object(sys, "platform", "linux"))
        self._start(
            mock.patch(
                "apps.checkers.checkers.listening_ports.psutil.net_connections",
                side_effect=psutil.AccessDenied(),
            )
        )
        result = ListeningPortsChecker().check()
        self.assertEqual(result.status, CheckStatus.OK)
        self.assertIn("cannot read listening ports", result.message)

    def test_registered_in_registry(self):
        from apps.checkers.checkers import CHECKER_REGISTRY
        from apps.checkers.checkers import ListeningPortsChecker as Exported

        self.assertIs(CHECKER_REGISTRY["listening_ports"], Exported)
