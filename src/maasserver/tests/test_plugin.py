# Copyright 2014-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for the ``maasregiond`` TAP."""

__all__ = []

import crochet
from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper
from maasserver import eventloop
from maasserver.plugin import (
    Options,
    RegionServiceMaker,
)
from maasserver.utils.orm import (
    disable_all_database_connections,
    DisabledDatabaseConnection,
    enable_all_database_connections,
)
from maastesting.matchers import MockCalledOnceWith
from maastesting.testcase import MAASTestCase
from provisioningserver import logger
from provisioningserver.utils.twisted import (
    asynchronous,
    ThreadPool,
)
from testtools import monkey
from testtools.matchers import IsInstance
from twisted.application.service import MultiService
from twisted.internet import reactor


def import_websocket_handlers():
    # Import the websocket handlers for their side-effects: merely defining
    # DeviceHandler, e.g., causes a database access, which will crash if it
    # happens inside the reactor thread where database access is forbidden and
    # prevented. The most sensible solution to this might be to disallow
    # database access at import time.
    import maasserver.websockets.handlers  # noqa


class TestOptions(MAASTestCase):
    """Tests for `maasserver.plugin.Options`."""

    def test_defaults(self):
        options = Options()
        self.assertEqual({}, options.defaults)

    def test_parse_minimal_options(self):
        options = Options()
        # The minimal set of options that must be provided.
        arguments = []
        options.parseOptions(arguments)  # No error.


class TestRegionServiceMaker(MAASTestCase):
    """Tests for `maasserver.plugin.RegionServiceMaker`."""

    def setUp(self):
        super(TestRegionServiceMaker, self).setUp()
        self.patch(eventloop.loop, "services", MultiService())
        self.patch_autospec(crochet, "no_setup")
        self.patch_autospec(logger, "configure")
        # Enable database access in the reactor just for these tests.
        asynchronous(enable_all_database_connections, timeout=5)()
        import_websocket_handlers()

    def tearDown(self):
        super(TestRegionServiceMaker, self).tearDown()
        # Disable database access in the reactor again.
        asynchronous(disable_all_database_connections, timeout=5)()

    def test_init(self):
        service_maker = RegionServiceMaker("Harry", "Hill")
        self.assertEqual("Harry", service_maker.tapname)
        self.assertEqual("Hill", service_maker.description)

    @asynchronous(timeout=5)
    def test_makeService(self):
        options = Options()
        service_maker = RegionServiceMaker("Harry", "Hill")
        # Look like the master process.
        self.patch(eventloop, "is_master_process").return_value = True
        # Disable _ensureConnection() its not allowed in the reactor.
        self.patch_autospec(service_maker, "_ensureConnection")
        # Disable _configureThreads() as it's too invasive right now.
        self.patch_autospec(service_maker, "_configureThreads")
        # Disable _preformStartUp() as it performs operations we don't want
        # in the testing environment.
        self.patch_autospec(service_maker, "_performStartUp")
        service = service_maker.makeService(options)
        self.assertIsInstance(service, MultiService)
        expected_services = [
            "active-discovery",
            "database-tasks",
            "dns-publication-cleanup",
            "import-resources",
            "import-resources-progress",
            "networks-monitor",
            "nonce-cleanup",
            "ntp",
            "postgres-listener",
            "rack-controller",
            "region-controller",
            "rpc",
            "rpc-advertise",
            "service-monitor",
            "status-monitor",
            "web",
        ]
        self.assertItemsEqual(expected_services, service.namedServices)
        self.assertEqual(
            len(service.namedServices), len(service.services),
            "Not all services are named.")
        self.assertThat(
            logger.configure, MockCalledOnceWith(
                options["verbosity"], logger.LoggingMode.TWISTD))
        self.assertThat(crochet.no_setup, MockCalledOnceWith())

    @asynchronous(timeout=5)
    def test_configures_thread_pool(self):
        # Patch and restore where it's visible because patching a running
        # reactor is potentially fairly harmful.
        patcher = monkey.MonkeyPatcher()
        patcher.add_patch(reactor, "threadpool", None)
        patcher.add_patch(reactor, "threadpoolForDatabase", None)
        patcher.patch()
        try:
            service_maker = RegionServiceMaker("Harry", "Hill")
            # Disable _ensureConnection() its not allowed in the reactor.
            self.patch_autospec(service_maker, "_ensureConnection")
            # Disable _preformStartUp() as it performs operations we don't want
            # in the testing environment.
            self.patch_autospec(service_maker, "_performStartUp")
            service_maker.makeService(Options())
            threadpool = reactor.getThreadPool()
            self.assertThat(threadpool, IsInstance(ThreadPool))
        finally:
            patcher.restore()

    def assertConnectionsEnabled(self):
        for alias in connections:
            self.assertThat(
                connections[alias],
                IsInstance(BaseDatabaseWrapper))

    def assertConnectionsDisabled(self):
        for alias in connections:
            self.assertEqual(
                DisabledDatabaseConnection,
                type(connections[alias]))

    @asynchronous(timeout=5)
    def test_disables_database_connections_in_reactor(self):
        self.assertConnectionsEnabled()
        service_maker = RegionServiceMaker("Harry", "Hill")
        # Disable _ensureConnection() its not allowed in the reactor.
        self.patch_autospec(service_maker, "_ensureConnection")
        # Disable _configureThreads() as it's too invasive right now.
        self.patch_autospec(service_maker, "_configureThreads")
        # Disable _preformStartUp() as it performs operations we don't want
        # in the testing environment.
        self.patch_autospec(service_maker, "_performStartUp")
        service_maker.makeService(Options())
        self.assertConnectionsDisabled()

    @asynchronous(timeout=5)
    def test_runs_start_up(self):
        service_maker = RegionServiceMaker("Harry", "Hill")
        # Disable _ensureConnection() its not allowed in the reactor.
        self.patch_autospec(service_maker, "_ensureConnection")
        # Disable _configureThreads() as it's too invasive right now.
        self.patch_autospec(service_maker, "_configureThreads")
        # Disable _preformStartUp() as it performs operations we don't want
        # in the testing environment.
        mock_performStartUp = self.patch_autospec(
            service_maker, "_performStartUp")
        service_maker.makeService(Options())
        self.assertThat(mock_performStartUp, MockCalledOnceWith())
