# Copyright 2014-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for `maasserver.eventloop`."""

__all__ = []

import socket

from crochet import wait_for
from django.db import connections
from maasserver import (
    bootresources,
    eventloop,
    nonces_cleanup,
    service_monitor,
    status_monitor,
    system_controller,
    webapp,
)
from maasserver.eventloop import DEFAULT_PORT
from maasserver.rpc import regionservice
from maasserver.testing.eventloop import RegionEventLoopFixture
from maasserver.testing.listener import FakePostgresListenerService
from maasserver.utils import dbtasks
from maasserver.utils.orm import (
    DisabledDatabaseConnection,
    transactional,
)
from maastesting.factory import factory
from maastesting.testcase import MAASTestCase
from mock import sentinel
from testtools.matchers import (
    Equals,
    IsInstance,
    MatchesStructure,
)
from twisted.internet import (
    defer,
    reactor,
)
from twisted.python.threadable import isInIOThread


wait_for_reactor = wait_for(30)  # 30 seconds.


class TestRegionEventLoop(MAASTestCase):

    def test_name(self):
        self.patch(eventloop, "gethostname").return_value = "foo"
        self.patch(eventloop, "getpid").return_value = 12345
        self.assertEqual("foo:pid=12345", eventloop.loop.name)

    def test_populate(self):
        self.patch(eventloop.services, "getServiceNamed")
        an_eventloop = eventloop.RegionEventLoop()
        # At first there are no services.
        self.assertEqual(
            set(), {service.name for service in an_eventloop.services})
        # populate() creates a service with each factory.
        an_eventloop.populate().wait(30)
        self.assertEqual(
            {name for name in an_eventloop.factories.keys()},
            {svc.name for svc in an_eventloop.services})
        # The services are not started.
        self.assertEqual(
            {name: False for name in an_eventloop.factories.keys()},
            {svc.name: svc.running for svc in an_eventloop.services})

    def test_start_and_stop(self):
        # Replace the factories in RegionEventLoop with non-functional
        # dummies to avoid bringing up real services here, and ensure
        # that the services list is empty.
        self.useFixture(RegionEventLoopFixture())
        # At the outset, the eventloop's services are dorment.
        self.assertFalse(eventloop.loop.services.running)
        # RegionEventLoop.running is an alias for .services.running.
        self.assertFalse(eventloop.loop.running)
        self.assertEqual(
            set(eventloop.loop.services),
            set())
        # Patch prepare so it's not actually run.
        self.patch(eventloop.loop, "prepare").return_value = (
            defer.succeed(None))
        # After starting the loop, the services list is populated, and
        # the services are started too.
        eventloop.loop.start().wait(5)
        self.addCleanup(lambda: eventloop.loop.reset().wait(5))
        self.assertTrue(eventloop.loop.services.running)
        self.assertTrue(eventloop.loop.running)
        self.assertEqual(
            {service.name for service in eventloop.loop.services},
            {name for name in eventloop.loop.factories.keys()})
        # A shutdown hook is registered with the reactor.
        stopService = eventloop.loop.services.stopService
        self.assertEqual(
            ("shutdown", ("before", stopService, (), {})),
            eventloop.loop.handle)
        # After stopping the loop, the services list remains populated,
        # but the services are all stopped.
        eventloop.loop.stop().wait(5)
        self.assertFalse(eventloop.loop.services.running)
        self.assertFalse(eventloop.loop.running)
        self.assertEqual(
            {service.name for service in eventloop.loop.services},
            {name for name in eventloop.loop.factories.keys()})
        # The hook has been cleared.
        self.assertIsNone(eventloop.loop.handle)

    def test_reset(self):
        # Replace the factories in RegionEventLoop with non-functional
        # dummies to avoid bringing up real services here, and ensure
        # that the services list is empty.
        self.useFixture(RegionEventLoopFixture())
        # Patch prepare so it's not actually run.
        self.patch(eventloop.loop, "prepare").return_value = (
            defer.succeed(None))
        eventloop.loop.start().wait(5)
        eventloop.loop.reset().wait(5)
        # After stopping the loop, the services list is also emptied.
        self.assertFalse(eventloop.loop.services.running)
        self.assertFalse(eventloop.loop.running)
        self.assertEqual(
            set(eventloop.loop.services),
            set())
        # The hook has also been cleared.
        self.assertIsNone(eventloop.loop.handle)

    def test_reset_clears_factories(self):
        eventloop.loop.factories = (
            (factory.make_name("service"), None),
        )
        eventloop.loop.reset().wait(5)
        # The loop's factories are also reset.
        self.assertEqual(
            eventloop.loop.__class__.factories,
            eventloop.loop.factories)

    def test_module_globals(self):
        # Several module globals are references to a shared RegionEventLoop.
        self.assertIs(eventloop.services, eventloop.loop.services)
        # Must compare by equality here; these methods are decorated.
        self.assertEqual(eventloop.reset, eventloop.loop.reset)
        self.assertEqual(eventloop.start, eventloop.loop.start)
        self.assertEqual(eventloop.stop, eventloop.loop.stop)


class TestFactories(MAASTestCase):

    def test_make_DatabaseTaskService(self):
        service = eventloop.make_DatabaseTaskService()
        self.assertThat(service, IsInstance(dbtasks.DatabaseTasksService))
        # It is registered as a factory in RegionEventLoop.
        self.assertIs(
            eventloop.make_DatabaseTaskService,
            eventloop.loop.factories["database-tasks"]["factory"])

    def test_make_RegionService(self):
        service = eventloop.make_RegionService()
        self.assertThat(service, IsInstance(regionservice.RegionService))
        # It is registered as a factory in RegionEventLoop.
        self.assertIs(
            eventloop.make_RegionService,
            eventloop.loop.factories["rpc"]["factory"])

    def test_make_RegionAdvertisingService(self):
        service = eventloop.make_RegionAdvertisingService()
        self.assertThat(service, IsInstance(
            regionservice.RegionAdvertisingService))
        # It is registered as a factory in RegionEventLoop.
        self.assertIs(
            eventloop.make_RegionAdvertisingService,
            eventloop.loop.factories["rpc-advertise"]["factory"])

    def test_make_NonceCleanupService(self):
        service = eventloop.make_NonceCleanupService()
        self.assertThat(service, IsInstance(
            nonces_cleanup.NonceCleanupService))
        # It is registered as a factory in RegionEventLoop.
        self.assertIs(
            eventloop.make_NonceCleanupService,
            eventloop.loop.factories["nonce-cleanup"]["factory"])

    def test_make_StatusMonitorService(self):
        service = eventloop.make_StatusMonitorService()
        self.assertThat(service, IsInstance(
            status_monitor.StatusMonitorService))
        # It is registered as a factory in RegionEventLoop.
        self.assertIs(
            eventloop.make_StatusMonitorService,
            eventloop.loop.factories["status-monitor"]["factory"])

    def test_make_ImportResourcesService(self):
        service = eventloop.make_ImportResourcesService()
        self.assertThat(service, IsInstance(
            bootresources.ImportResourcesService))
        # It is registered as a factory in RegionEventLoop.
        self.assertIs(
            eventloop.make_ImportResourcesService,
            eventloop.loop.factories["import-resources"]["factory"])

    def test_make_WebApplicationService(self):
        service = eventloop.make_WebApplicationService(
            FakePostgresListenerService())
        self.assertThat(service, IsInstance(webapp.WebApplicationService))
        # The endpoint is set to port 5243 on localhost.
        self.assertThat(service.endpoint, MatchesStructure.byEquality(
            reactor=reactor, addressFamily=socket.AF_INET))
        self.assertThat(
            service.endpoint.port, Equals(DEFAULT_PORT))
        self.assertThat(
            service.endpoint.socket.getsockname(),
            Equals(("0.0.0.0", DEFAULT_PORT)))
        # It is registered as a factory in RegionEventLoop.
        self.assertIs(
            eventloop.make_WebApplicationService,
            eventloop.loop.factories["web"]["factory"])
        # Has a dependency of postgres-listener.
        self.assertEquals(
            ["postgres-listener"],
            eventloop.loop.factories["web"]["requires"])

    def test_make_SystemControllerService(self):
        service = eventloop.make_SystemControllerService(
            FakePostgresListenerService(), sentinel.rpc_advertise)
        self.assertThat(service, IsInstance(
            system_controller.SystemControllerService))
        # It is registered as a factory in RegionEventLoop.
        self.assertIs(
            eventloop.make_SystemControllerService,
            eventloop.loop.factories["system-controller"]["factory"])
        # Has a dependency of postgres-listener and rpc-advertise.
        self.assertEquals(
            ["postgres-listener", "rpc-advertise"],
            eventloop.loop.factories["system-controller"]["requires"])

    def test_make_ServiceMonitorService(self):
        service = eventloop.make_ServiceMonitorService(
            sentinel.rpc_advertise)
        self.assertThat(service, IsInstance(
            service_monitor.ServiceMonitorService))
        # It is registered as a factory in RegionEventLoop.
        self.assertIs(
            eventloop.make_ServiceMonitorService,
            eventloop.loop.factories["service-monitor"]["factory"])
        # Has a dependency of rpc-advertise.
        self.assertEquals(
            ["rpc-advertise"],
            eventloop.loop.factories["service-monitor"]["requires"])


class TestDisablingDatabaseConnections(MAASTestCase):

    @wait_for_reactor
    def test_connections_are_all_stubs_in_the_event_loop(self):
        self.assertTrue(isInIOThread())
        for alias in connections:
            connection = connections[alias]
            # isinstance() fails because it references __bases__, so
            # compare types here.
            self.assertEqual(
                DisabledDatabaseConnection,
                type(connection))

    @transactional
    def test_connections_are_all_usable_outside_the_event_loop(self):
        self.assertFalse(isInIOThread())
        for alias in connections:
            connection = connections[alias]
            self.assertTrue(connection.is_usable())
