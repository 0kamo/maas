# Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Fixtures for testing `power_parameters`."""

__all__ = [
    'StaticPowerTypesFixture',
    ]

from unittest.mock import Mock

from fixtures import Fixture
from maasserver.clusterrpc import power_parameters
from provisioningserver.power import schema
from testtools import monkey


class StaticPowerTypesFixture(Fixture):
    """Prevents communication with clusters when querying power types.

    This patches out the `get_all_power_types_from_clusters` call. It's a
    common enough requirement that it's been folded into a fixture.
    """

    def setUp(self):
        super(StaticPowerTypesFixture, self).setUp()
        # This patch prevents communication with a non-existent cluster
        # controller when fetching power types.
        restore = monkey.patch(
            power_parameters, 'get_all_power_types_from_clusters',
            Mock(return_value=schema.JSON_POWER_TYPE_PARAMETERS))
        self.addCleanup(restore)
