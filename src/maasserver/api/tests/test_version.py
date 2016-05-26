# Copyright 2014-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Test maasserver API version."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []


import httplib
import json

from django.contrib.auth.models import AnonymousUser
from django.core.urlresolvers import reverse
from maasserver.api.version import API_CAPABILITIES_LIST
from maasserver.testing.api import MultipleUsersScenarios
from maasserver.testing.factory import factory
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.utils import version as version_module


class TestVersionAPIBasics(MAASServerTestCase):
    """Basic tests for /version/ API."""

    def test_handler_path(self):
        self.assertEqual(
            '/api/1.0/version/', reverse('version_handler'))


class TestVersionAPI(MultipleUsersScenarios, MAASServerTestCase):
    """Tests for /version/ API."""

    scenarios = [
        ('anon', dict(userfactory=AnonymousUser)),
        ('user', dict(userfactory=factory.make_User)),
    ]

    def test_GET_returns_details(self):
        mock_apt = self.patch(version_module, "get_version_from_apt")
        mock_apt.return_value = "1.8.0~alpha4+bzr356-0ubuntu1"
        self.patch(version_module, "_cache", {})

        response = self.client.get(reverse('version_handler'))
        self.assertEqual(httplib.OK, response.status_code)

        parsed_result = json.loads(response.content)
        self.assertEqual(
            {
                'capabilities': API_CAPABILITIES_LIST,
                'version': '1.8.0',
                'subversion': 'alpha4+bzr356',
            },
            parsed_result)
