# Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for :py:module:`~provisioningserver.power.query`."""

__all__ = []

import logging
import random
from unittest.mock import (
    ANY,
    call,
    sentinel,
)

from fixtures import FakeLogger
from maastesting.factory import factory
from maastesting.matchers import (
    MockCalledOnceWith,
    MockCalledWith,
    MockCallsMatch,
)
from maastesting.runtest import MAASTwistedRunTest
from maastesting.testcase import MAASTestCase
from maastesting.twisted import (
    always_fail_with,
    extract_result,
    TwistedLoggerFixture,
)
from provisioningserver import power
from provisioningserver.drivers.power import (
    DEFAULT_WAITING_POLICY,
    power_drivers_by_name,
    PowerDriverRegistry,
    PowerError,
)
from provisioningserver.events import EVENT_TYPES
from provisioningserver.rpc import (
    exceptions,
    region,
)
from provisioningserver.rpc.testing import MockClusterToRegionRPCFixture
from provisioningserver.testing.events import EventTypesAllRegistered
from testtools.deferredruntest import assert_fails_with
from testtools.matchers import Not
from twisted.internet import reactor
from twisted.internet.defer import (
    fail,
    inlineCallbacks,
    maybeDeferred,
    succeed,
)
from twisted.internet.task import Clock
from twisted.python.failure import Failure


def suppress_reporting(test):
    # Skip telling the region; just pass-through the query result.
    report_power_state = test.patch(power.query, "report_power_state")
    report_power_state.side_effect = lambda d, system_id, hostname: d


class TestPowerHelpers(MAASTestCase):

    run_tests_with = MAASTwistedRunTest.make_factory(timeout=5)

    def setUp(self):
        super(TestPowerHelpers, self).setUp()
        self.useFixture(EventTypesAllRegistered())

    def patch_rpc_methods(self):
        fixture = self.useFixture(MockClusterToRegionRPCFixture())
        protocol, io = fixture.makeEventLoop(
            region.MarkNodeFailed, region.UpdateNodePowerState,
            region.SendEvent)
        return protocol, io

    def test_power_query_failure_emits_event(self):
        system_id = factory.make_name('system_id')
        hostname = factory.make_name('hostname')
        message = factory.make_name('message')
        protocol, io = self.patch_rpc_methods()
        d = power.query.power_query_failure(
            system_id, hostname, Failure(Exception(message)))
        # This blocks until the deferred is complete.
        io.flush()
        self.assertIsNone(extract_result(d))
        self.assertThat(
            protocol.SendEvent,
            MockCalledOnceWith(
                ANY, type_name=EVENT_TYPES.NODE_POWER_QUERY_FAILED,
                system_id=system_id, description=message))

    def test_power_query_success_emits_event(self):
        system_id = factory.make_name('system_id')
        hostname = factory.make_name('hostname')
        state = factory.make_name('state')
        message = "Power state queried: %s" % state
        protocol, io = self.patch_rpc_methods()
        d = power.query.power_query_success(
            system_id, hostname, state)
        # This blocks until the deferred is complete.
        io.flush()
        self.assertIsNone(extract_result(d))
        self.assertThat(
            protocol.SendEvent,
            MockCalledOnceWith(
                ANY, type_name=EVENT_TYPES.NODE_POWER_QUERIED_DEBUG,
                system_id=system_id, description=message))


class TestPowerQuery(MAASTestCase):

    run_tests_with = MAASTwistedRunTest.make_factory(timeout=5)

    def setUp(self):
        super(TestPowerQuery, self).setUp()
        self.useFixture(EventTypesAllRegistered())
        self.patch(power.query, "deferToThread", maybeDeferred)
        for power_driver in power_drivers_by_name.values():
            self.patch(
                power_driver, "detect_missing_packages").return_value = []

    def patch_rpc_methods(self, return_value={}, side_effect=None):
        fixture = self.useFixture(MockClusterToRegionRPCFixture())
        protocol, io = fixture.makeEventLoop(
            region.MarkNodeFailed, region.SendEvent,
            region.UpdateNodePowerState)
        protocol.MarkNodeFailed.return_value = return_value
        protocol.MarkNodeFailed.side_effect = side_effect
        return protocol.SendEvent, protocol.MarkNodeFailed, io

    def test_get_power_state_queries_node(self):
        system_id = factory.make_name('system_id')
        hostname = factory.make_name('hostname')
        power_type = random.choice(power.QUERY_POWER_TYPES)
        power_state = random.choice(['on', 'off'])
        context = {
            factory.make_name('context-key'): factory.make_name('context-val')
        }
        self.patch(power, 'is_driver_available').return_value = True
        _, markNodeBroken, io = self.patch_rpc_methods()
        mock_perform_power_driver_query = self.patch(
            power.query, 'perform_power_driver_query')
        mock_perform_power_driver_query.return_value = power_state

        d = power.query.get_power_state(
            system_id, hostname, power_type, context)
        # This blocks until the deferred is complete.
        io.flush()
        self.assertEqual(power_state, extract_result(d))
        self.assertThat(
            power_drivers_by_name.get(power_type).detect_missing_packages,
            MockCalledOnceWith())
        self.assertThat(
            mock_perform_power_driver_query,
            MockCallsMatch(
                call(system_id, hostname, power_type, context)
            ),
        )

    def test_get_power_state_fails_for_missing_packages(self):
        system_id = factory.make_name('system_id')
        hostname = factory.make_name('hostname')
        power_type = random.choice(power.QUERY_POWER_TYPES)
        context = {
            factory.make_name('context-key'): factory.make_name('context-val')
        }
        self.patch(power, 'is_driver_available').return_value = False
        _, markNodeBroken, io = self.patch_rpc_methods()

        power_driver = power_drivers_by_name.get(power_type)
        power_driver.detect_missing_packages.return_value = ['gone']

        d = power.query.get_power_state(
            system_id, hostname, power_type, context)
        # This blocks until the deferred is complete.
        io.flush()

        self.assertThat(
            power_driver.detect_missing_packages, MockCalledOnceWith())
        return assert_fails_with(d, exceptions.PowerActionFail)

    def test_report_power_state_changes_power_state_if_failure(self):
        system_id = factory.make_name('system_id')
        hostname = factory.make_name('hostname')
        err_msg = factory.make_name('error')

        _, _, io = self.patch_rpc_methods()
        self.patch_autospec(power, 'power_state_update')

        # Simulate a failure when querying state.
        query = fail(exceptions.PowerActionFail(err_msg))
        report = power.query.report_power_state(query, system_id, hostname)
        # This blocks until the deferred is complete.
        io.flush()

        error = self.assertRaises(
            exceptions.PowerActionFail, extract_result, report)
        self.assertEqual(err_msg, str(error))
        self.assertThat(
            power.power_state_update,
            MockCalledOnceWith(system_id, 'error'))

    def test_report_power_state_changes_power_state_if_success(self):
        system_id = factory.make_name('system_id')
        hostname = factory.make_name('hostname')
        power_state = random.choice(['on', 'off'])

        _, _, io = self.patch_rpc_methods()
        self.patch_autospec(power, 'power_state_update')

        # Simulate a success when querying state.
        query = succeed(power_state)
        report = power.query.report_power_state(query, system_id, hostname)
        # This blocks until the deferred is complete.
        io.flush()

        self.assertEqual(power_state, extract_result(report))
        self.assertThat(
            power.power_state_update,
            MockCalledOnceWith(system_id, power_state))

    def test_report_power_state_changes_power_state_if_unknown(self):
        system_id = factory.make_name('system_id')
        hostname = factory.make_name('hostname')
        power_state = "unknown"

        _, _, io = self.patch_rpc_methods()
        self.patch_autospec(power, 'power_state_update')

        # Simulate a success when querying state.
        query = succeed(power_state)
        report = power.query.report_power_state(query, system_id, hostname)
        # This blocks until the deferred is complete.
        io.flush()

        self.assertEqual(power_state, extract_result(report))
        self.assertThat(
            power.power_state_update,
            MockCalledOnceWith(system_id, power_state))


class TestPowerQueryExceptions(MAASTestCase):

    scenarios = tuple(
        (power_type, {
            "power_type": power_type,
            "power_driver": power_drivers_by_name.get(power_type),
            "func": (  # Function to invoke power driver.
                "perform_power_driver_query"),
            "waits": (  # Pauses between retries.
                [] if power_type in PowerDriverRegistry
                else DEFAULT_WAITING_POLICY),
            "calls": (  # No. of calls to the driver.
                1 if power_type in PowerDriverRegistry
                else len(DEFAULT_WAITING_POLICY)),
        })
        for power_type in power.QUERY_POWER_TYPES
    )

    def test_report_power_state_reports_all_exceptions(self):
        logger_twisted = self.useFixture(TwistedLoggerFixture())
        logger_maaslog = self.useFixture(FakeLogger("maas"))

        # Avoid threads here.
        self.patch(power.query, "deferToThread", maybeDeferred)

        exception_type = factory.make_exception_type()
        exception_message = factory.make_string()
        exception = exception_type(exception_message)

        # Pretend the query always fails with `exception`.
        query = self.patch_autospec(power.query, self.func)
        query.side_effect = always_fail_with(exception)

        # Intercept calls to power_state_update() and send_event_node().
        power_state_update = self.patch_autospec(power, "power_state_update")
        power_state_update.return_value = succeed(None)
        send_event_node = self.patch_autospec(power.query, "send_event_node")
        send_event_node.return_value = succeed(None)

        self.patch(
            self.power_driver, "detect_missing_packages").return_value = []

        system_id = factory.make_name('system_id')
        hostname = factory.make_name('hostname')
        context = sentinel.context
        clock = Clock()

        d = power.query.get_power_state(
            system_id, hostname, self.power_type, context, clock)
        d = power.query.report_power_state(
            d, system_id, hostname)

        # Crank through some number of retries.
        for wait in self.waits:
            self.assertFalse(d.called)
            clock.advance(wait)
        self.assertTrue(d.called)

        # Finally the exception from the query is raised.
        self.assertRaises(exception_type, extract_result, d)

        # The broken power query function patched earlier was called the same
        # number of times as there are steps in the default waiting policy.
        expected_call = call(system_id, hostname, self.power_type, context)
        expected_calls = [expected_call] * self.calls
        self.assertThat(query, MockCallsMatch(*expected_calls))

        expected_message = (
            "%s: Power state could not be queried: %s" % (
                hostname, exception_message))

        # An attempt was made to report the failure to the region.
        self.assertThat(
            power_state_update, MockCalledOnceWith(system_id, 'error'))
        # An attempt was made to log a node event with details.
        self.assertThat(
            send_event_node, MockCalledOnceWith(
                EVENT_TYPES.NODE_POWER_QUERY_FAILED,
                system_id, hostname, exception_message))

        # Nothing was logged to the Twisted log.
        self.assertEqual("", logger_twisted.output)
        # A brief message is written to maaslog.
        self.assertEqual(expected_message + "\n", logger_maaslog.output)


class TestPowerQueryAsync(MAASTestCase):

    run_tests_with = MAASTwistedRunTest.make_factory(timeout=5)

    def setUp(self):
        super(TestPowerQueryAsync, self).setUp()

    def make_node(self, power_type=None):
        system_id = factory.make_name('system_id')
        hostname = factory.make_name('hostname')
        if power_type is None:
            power_type = random.choice(power.QUERY_POWER_TYPES)
        state = random.choice(['on', 'off', 'unknown', 'error'])
        context = {
            factory.make_name('context-key'): (
                factory.make_name('context-val'))
        }
        return {
            'context': context,
            'hostname': hostname,
            'power_state': state,
            'power_type': power_type,
            'system_id': system_id,
        }

    def make_nodes(self, count=3):
        nodes = [self.make_node() for _ in range(count)]
        # Sanity check that these nodes are something that can emerge
        # from a call to ListNodePowerParameters.
        region.ListNodePowerParameters.makeResponse({"nodes": nodes}, None)
        return nodes

    def pick_alternate_state(self, state):
        return random.choice([
            value for value in ['on', 'off', 'unknown', 'error']
            if value != state])

    @inlineCallbacks
    def test_query_all_nodes_gets_and_reports_power_state(self):
        nodes = self.make_nodes()

        # Report back that all nodes' power states are as recorded.
        power_states = [node['power_state'] for node in nodes]
        queries = list(map(succeed, power_states))
        get_power_state = self.patch(power.query, 'get_power_state')
        get_power_state.side_effect = queries
        report_power_state = self.patch(power.query, 'report_power_state')
        report_power_state.side_effect = lambda d, sid, hn: d

        yield power.query.query_all_nodes(nodes)
        self.assertThat(get_power_state, MockCallsMatch(*(
            call(
                node['system_id'], node['hostname'],
                node['power_type'], node['context'],
                clock=reactor)
            for node in nodes
        )))
        self.assertThat(report_power_state, MockCallsMatch(*(
            call(query, node['system_id'], node['hostname'])
            for query, node in zip(queries, nodes)
        )))

    @inlineCallbacks
    def test_query_all_nodes_logs_skip_if_node_in_action_registry(self):
        node = self.make_node()
        power.power_action_registry[node['system_id']] = sentinel.action
        with FakeLogger("maas.power", level=logging.DEBUG) as maaslog:
            yield power.query.query_all_nodes([node])
        self.assertDocTestMatches(
            "hostname-...: Skipping query power status, "
            "power action already in progress.",
            maaslog.output)

    @inlineCallbacks
    def test_query_all_nodes_skips_nodes_in_action_registry(self):
        nodes = self.make_nodes()

        # First node is in the registry.
        power.power_action_registry[nodes[0]['system_id']] = sentinel.action

        # Report back power state of nodes' not in registry.
        power_states = [node['power_state'] for node in nodes[1:]]
        get_power_state = self.patch(power.query, 'get_power_state')
        get_power_state.side_effect = map(succeed, power_states)
        suppress_reporting(self)

        yield power.query.query_all_nodes(nodes)
        self.assertThat(get_power_state, MockCallsMatch(*(
            call(
                node['system_id'], node['hostname'],
                node['power_type'], node['context'],
                clock=reactor)
            for node in nodes[1:]
        )))
        self.assertThat(
            get_power_state, Not(MockCalledWith(
                nodes[0]['system_id'], nodes[0]['hostname'],
                nodes[0]['power_type'], nodes[0]['context'],
                clock=reactor)))

    @inlineCallbacks
    def test_query_all_nodes_only_queries_queryable_power_types(self):
        nodes = self.make_nodes()
        # nodes are all queryable, so add one that isn't:
        nodes.append(self.make_node(power_type='manual'))

        # Report back that all nodes' power states are as recorded.
        power_states = [node['power_state'] for node in nodes]
        get_power_state = self.patch(power.query, 'get_power_state')
        get_power_state.side_effect = map(succeed, power_states)
        suppress_reporting(self)

        yield power.query.query_all_nodes(nodes)
        self.assertThat(get_power_state, MockCallsMatch(*(
            call(
                node['system_id'], node['hostname'],
                node['power_type'], node['context'],
                clock=reactor)
            for node in nodes
            if node['power_type'] in power.QUERY_POWER_TYPES
        )))

    @inlineCallbacks
    def test_query_all_nodes_swallows_PowerActionFail(self):
        node1, node2 = self.make_nodes(2)
        new_state_2 = self.pick_alternate_state(node2['power_state'])
        get_power_state = self.patch(power.query, 'get_power_state')
        error_msg = factory.make_name("error")
        get_power_state.side_effect = [
            fail(exceptions.PowerActionFail(error_msg)),
            succeed(new_state_2),
        ]
        suppress_reporting(self)

        with FakeLogger("maas.power", level=logging.DEBUG) as maaslog:
            yield power.query.query_all_nodes([node1, node2])

        self.assertDocTestMatches(
            """\
            hostname-...: Could not query power state: %s.
            hostname-...: Power state has changed from ... to ...
            """ % error_msg,
            maaslog.output)

    @inlineCallbacks
    def test_query_all_nodes_swallows_PowerError(self):
        node1, node2 = self.make_nodes(2)
        new_state_2 = self.pick_alternate_state(node2['power_state'])
        get_power_state = self.patch(power.query, 'get_power_state')
        error_msg = factory.make_name("error")
        get_power_state.side_effect = [
            fail(PowerError(error_msg)),
            succeed(new_state_2),
        ]
        suppress_reporting(self)

        with FakeLogger("maas.power", level=logging.DEBUG) as maaslog:
            yield power.query.query_all_nodes([node1, node2])

        self.assertDocTestMatches(
            """\
            %s: Could not query power state: %s.
            %s: Power state has changed from %s to %s.
            """ % (node1['hostname'], error_msg,
                   node2['hostname'], node2['power_state'], new_state_2),
            maaslog.output)

    @inlineCallbacks
    def test_query_all_nodes_swallows_NoSuchNode(self):
        node1, node2 = self.make_nodes(2)
        new_state_2 = self.pick_alternate_state(node2['power_state'])
        get_power_state = self.patch(power.query, 'get_power_state')
        get_power_state.side_effect = [
            fail(exceptions.NoSuchNode()),
            succeed(new_state_2),
        ]
        suppress_reporting(self)

        with FakeLogger("maas.power", level=logging.DEBUG) as maaslog:
            yield power.query.query_all_nodes([node1, node2])

        self.assertDocTestMatches(
            """\
            hostname-...: Could not update power state: no such node.
            hostname-...: Power state has changed from ... to ...
            """,
            maaslog.output)

    @inlineCallbacks
    def test_query_all_nodes_swallows_Exception(self):
        node1, node2 = self.make_nodes(2)
        error_message = factory.make_name("error")
        error_type = factory.make_exception_type()
        new_state_2 = self.pick_alternate_state(node2['power_state'])
        get_power_state = self.patch(power.query, 'get_power_state')
        get_power_state.side_effect = [
            fail(error_type(error_message)),
            succeed(new_state_2),
        ]
        suppress_reporting(self)

        maaslog = FakeLogger("maas.power", level=logging.DEBUG)
        twistlog = TwistedLoggerFixture()

        with maaslog, twistlog:
            yield power.query.query_all_nodes([node1, node2])

        self.assertDocTestMatches(
            """\
            hostname-...: Failed to refresh power state: %s
            hostname-...: Power state has changed from ... to ...
            """ % error_message,
            maaslog.output)

    @inlineCallbacks
    def test_query_all_nodes_returns_deferredlist_of_number_of_nodes(self):
        node1, node2 = self.make_nodes(2)
        get_power_state = self.patch(power.query, 'get_power_state')
        get_power_state.side_effect = [
            succeed(node1['power_state']),
            succeed(node2['power_state']),
        ]
        suppress_reporting(self)

        results = yield power.query.query_all_nodes([node1, node2])
        self.assertEqual(
            [(True, node1['power_state']), (True, node2['power_state'])],
            results)
