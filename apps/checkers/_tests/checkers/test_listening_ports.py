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
    resolve_processes,
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


class ResolveProcessesTests(TestCase):
    def _port(self, pid):
        return ListeningPort(ip="0.0.0.0", port=9999, family="ipv4", pid=pid, exposed=True)

    def test_stamps_name_and_username(self):
        proc = mock.Mock()
        proc.as_dict.return_value = {"name": "sshd", "username": "root"}
        p = self._port(100)
        with mock.patch("apps.checkers.checkers.listening_ports.psutil.Process", return_value=proc):
            resolve_processes([p])
        self.assertEqual(p.process, "sshd")
        self.assertEqual(p.username, "root")

    def test_pid_none_left_blank_and_not_looked_up(self):
        p = self._port(None)
        with mock.patch("apps.checkers.checkers.listening_ports.psutil.Process") as mock_proc:
            resolve_processes([p])
        self.assertIsNone(p.process)
        self.assertIsNone(p.username)
        mock_proc.assert_not_called()

    def test_no_such_process_leaves_blank(self):
        p = self._port(100)
        with mock.patch(
            "apps.checkers.checkers.listening_ports.psutil.Process",
            side_effect=psutil.NoSuchProcess(100),
        ):
            resolve_processes([p])
        self.assertIsNone(p.process)
        self.assertIsNone(p.username)

    def test_access_denied_field_is_none(self):
        # psutil as_dict(ad_value=None) yields None for inaccessible fields.
        proc = mock.Mock()
        proc.as_dict.return_value = {"name": None, "username": None}
        p = self._port(100)
        with mock.patch("apps.checkers.checkers.listening_ports.psutil.Process", return_value=proc):
            resolve_processes([p])
        self.assertIsNone(p.process)
        self.assertIsNone(p.username)

    def test_shared_pid_resolved_once(self):
        proc = mock.Mock()
        proc.as_dict.return_value = {"name": "nginx", "username": "www-data"}
        ports = [self._port(200), self._port(200)]  # same PID (IPv4 + IPv6)
        with mock.patch(
            "apps.checkers.checkers.listening_ports.psutil.Process", return_value=proc
        ) as mock_proc:
            resolve_processes(ports)
        mock_proc.assert_called_once()
        self.assertEqual([p.process for p in ports], ["nginx", "nginx"])


class ListeningPortsCheckerTests(TestCase):
    def _start(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch_conns(self, conns, proc_info=None):
        self._start(mock.patch.object(sys, "platform", "linux"))
        self._start(
            mock.patch(
                "apps.checkers.checkers.listening_ports.psutil.net_connections",
                return_value=conns,
            )
        )
        # Stub PID resolution so checker tests never touch the real system.
        proc = mock.Mock()
        proc.as_dict.return_value = proc_info or {"name": None, "username": None}
        self._start(
            mock.patch(
                "apps.checkers.checkers.listening_ports.psutil.Process",
                return_value=proc,
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

    @override_settings(LISTENING_PORTS_ALLOWLIST=[22])
    def test_metrics_and_message_include_process(self):
        self._patch_conns(
            [_conn("0.0.0.0", 9999)],
            proc_info={"name": "python3", "username": "deploy"},
        )
        result = ListeningPortsChecker().check()
        entry = result.metrics["listening"][0]
        self.assertEqual(entry["process"], "python3")
        self.assertEqual(entry["username"], "deploy")
        self.assertIn("[python3]", result.message)

    @override_settings(LISTENING_PORTS_ALLOWLIST=[22])
    def test_message_unknown_process_renders_question_mark(self):
        self._patch_conns([_conn("0.0.0.0", 9999)])  # default proc_info -> name None
        result = ListeningPortsChecker().check()
        self.assertIn("9999(0.0.0.0) [?]", result.message)

    def test_registered_in_registry(self):
        from apps.checkers.checkers import CHECKER_REGISTRY
        from apps.checkers.checkers import ListeningPortsChecker as Exported

        self.assertIs(CHECKER_REGISTRY["listening_ports"], Exported)
