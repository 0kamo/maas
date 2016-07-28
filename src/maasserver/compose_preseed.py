# Copyright 2012-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Low-level composition code for preseeds."""

__all__ = [
    'compose_preseed',
    ]

from datetime import timedelta
from urllib.parse import urlencode

from maasserver.clusterrpc.osystems import get_preseed_data
from maasserver.enum import (
    NODE_STATUS,
    PRESEED_TYPE,
)
from maasserver.models import PackageRepository
from maasserver.models.config import Config
from maasserver.server_address import get_maas_facing_server_host
from maasserver.utils import absolute_reverse
from provisioningserver.rpc.exceptions import (
    NoConnectionsAvailable,
    NoSuchOperatingSystem,
)
import yaml

# Default port for RSYSLOG
RSYSLOG_PORT = 514


def get_apt_proxy_for_node(node):
    """Return the APT proxy for the `node`."""
    if Config.objects.get_config("enable_http_proxy"):
        http_proxy = Config.objects.get_config("http_proxy")
        if http_proxy:
            return http_proxy
        else:
            return "http://%s:8000/" % get_maas_facing_server_host(
                node.get_boot_rack_controller())
    else:
        return None


def get_rsyslog_host_port(node):
    """Return the rsyslog host and port to use."""
    # TODO: In the future, we can make this configurable
    return "%s:%d" % (get_maas_facing_server_host(
        node.get_boot_rack_controller()), RSYSLOG_PORT)


def get_system_info():
    """Return the system info which includes the APT mirror information."""
    return {
        "system_info": {
            "package_mirrors": [
                {
                    "arches": ["i386", "amd64"],
                    "search": {
                        "primary": [
                            PackageRepository.get_main_archive()],
                        "security": [
                            PackageRepository.get_main_archive()],
                    },
                    "failsafe": {
                        "primary": "http://archive.ubuntu.com/ubuntu",
                        "security": "http://security.ubuntu.com/ubuntu",
                    }
                },
                {
                    "arches": ["default"],
                    "search": {
                        "primary": [
                            PackageRepository.get_ports_archive()],
                        "security": [
                            PackageRepository.get_ports_archive()],
                    },
                    "failsafe": {
                        "primary": "http://ports.ubuntu.com/ubuntu-ports",
                        "security": "http://ports.ubuntu.com/ubuntu-ports",
                    }
                },
            ]
        }
    }


def compose_cloud_init_preseed(node, token, base_url=''):
    """Compose the preseed value for a node in any state but Commissioning."""
    credentials = urlencode({
        'oauth_consumer_key': token.consumer.key,
        'oauth_token_key': token.key,
        'oauth_token_secret': token.secret,
        })

    config = {
        # Do not let cloud-init override /etc/hosts/: use the default
        # behavior which means running `dns_resolve(hostname)` on a node
        # will query the DNS server (and not return 127.0.0.1).
        # See bug 1087183 for details.
        "manage_etc_hosts": False,
        "apt_preserve_sources_list": True,
        # Prevent the node from requesting cloud-init data on every reboot.
        # This is done so a machine does not need to contact MAAS every time
        # it reboots.
        "manual_cache_clean": True,
        # This is used as preseed for a node that's been installed.
        # This will allow cloud-init to be configured with reporting for
        # a node that has already been installed.
        'reporting': {
            'maas': {
                'type': 'webhook',
                'endpoint': absolute_reverse(
                    'metadata-status', args=[node.system_id],
                    base_url=base_url),
                'consumer_key': token.consumer.key,
                'token_key': token.key,
                'token_secret': token.secret,
            }
        }
    }
    # Add the system configuration information.
    config.update(get_system_info())
    apt_proxy = get_apt_proxy_for_node(node)
    use_apt_proxy = (
        apt_proxy is not None and len(apt_proxy) > 0 and not
        apt_proxy.isspace())
    if use_apt_proxy:
        config['apt_proxy'] = apt_proxy
    local_config_yaml = yaml.safe_dump(config)
    # this is debconf escaping
    local_config = local_config_yaml.replace("\\", "\\\\").replace("\n", "\\n")

    # Preseed data to send to cloud-init.  We set this as MAAS_PRESEED in
    # ks_meta, and it gets fed straight into debconf.
    preseed_items = [
        ('datasources', 'multiselect', 'MAAS'),
        ('maas-metadata-url', 'string', absolute_reverse(
            'metadata', base_url=base_url)),
        ('maas-metadata-credentials', 'string', credentials),
        ('local-cloud-config', 'string', local_config)
        ]

    return '\n'.join(
        "cloud-init   cloud-init/%s  %s %s" % (
            item_name,
            item_type,
            item_value,
            )
        for item_name, item_type, item_value in preseed_items)


def compose_commissioning_preseed(node, token, base_url=''):
    """Compose the preseed value for a Commissioning node."""
    apt_proxy = get_apt_proxy_for_node(node)
    metadata_url = absolute_reverse('metadata', base_url=base_url)
    poweroff_timeout = timedelta(hours=1).total_seconds()  # 1 hour
    if node.status == NODE_STATUS.DISK_ERASING:
        poweroff_timeout = timedelta(days=7).total_seconds()  # 1 week
    return _compose_cloud_init_preseed(
        node, token, metadata_url, base_url=base_url, apt_proxy=apt_proxy,
        poweroff=True, poweroff_timeout=int(poweroff_timeout),
        poweroff_condition="test ! -e /tmp/block-poweroff")


def compose_curtin_preseed(node, token, base_url=''):
    """Compose the preseed value for a node being installed with curtin."""
    apt_proxy = get_apt_proxy_for_node(node)
    metadata_url = absolute_reverse('curtin-metadata', base_url=base_url)
    return _compose_cloud_init_preseed(
        node, token, metadata_url, base_url=base_url, apt_proxy=apt_proxy)


def _compose_cloud_init_preseed(
        node, token, metadata_url, base_url, apt_proxy=None,
        poweroff=False, poweroff_timeout=3600, poweroff_condition=None):
    cloud_config = {
        'datasource': {
            'MAAS': {
                'metadata_url': metadata_url,
                'consumer_key': token.consumer.key,
                'token_key': token.key,
                'token_secret': token.secret,
            }
        },
        # This configures reporting for the ephemeral environment
        'reporting': {
            'maas': {
                'type': 'webhook',
                'endpoint': absolute_reverse(
                    'metadata-status', args=[node.system_id],
                    base_url=base_url),
                'consumer_key': token.consumer.key,
                'token_key': token.key,
                'token_secret': token.secret,
            },
        },
        # This configure rsyslog for the ephemeral environment
        'rsyslog': {
            'remotes': {
                'maas': get_rsyslog_host_port(node),
            }
        },
    }
    # Add the system configuration information.
    cloud_config.update(get_system_info())
    if apt_proxy:
        cloud_config['apt_proxy'] = apt_proxy
    if poweroff:
        cloud_config['power_state'] = {
            'delay': 'now',
            'mode': 'poweroff',
            'timeout': poweroff_timeout,
        }
        if poweroff_condition is not None:
            cloud_config['power_state']['condition'] = poweroff_condition
    return "#cloud-config\n%s" % yaml.safe_dump(cloud_config)


def _get_metadata_url(preseed_type, base_url):
    if preseed_type == PRESEED_TYPE.CURTIN:
        return absolute_reverse('curtin-metadata', base_url=base_url)
    else:
        return absolute_reverse('metadata', base_url=base_url)


def compose_preseed(preseed_type, node):
    """Put together preseed data for `node`.

    This produces preseed data for the node in different formats depending
    on the preseed_type.

    :param preseed_type: The type of preseed to compose.
    :type preseed_type: string
    :param node: The node to compose preseed data for.
    :type node: Node
    :return: Preseed data containing the information the node needs in order
        to access the metadata service: its URL and auth token.
    """
    # Circular import.
    from metadataserver.models import NodeKey

    token = NodeKey.objects.get_token_for_node(node)
    rack_controller = node.get_boot_rack_controller()
    base_url = rack_controller.url

    if preseed_type == PRESEED_TYPE.COMMISSIONING:
        return compose_commissioning_preseed(node, token, base_url)
    else:
        metadata_url = _get_metadata_url(preseed_type, base_url)

        try:
            return get_preseed_data(preseed_type, node, token, metadata_url)
        except NotImplementedError:
            # This is fine; it indicates that the OS does not specify
            # any special preseed data for this type of preseed.
            pass
        except NoSuchOperatingSystem:
            # Let a caller handle this. If rendered for presentation in the
            # UI, an explanatory error message could be displayed. If rendered
            # via the API, in response to cloud-init for example, the prudent
            # course of action might be to turn the node's power off, mark it
            # as broken, and notify the user.
            raise
        except NoConnectionsAvailable:
            # This means that the region is not in contact with the node's
            # cluster controller. In the UI this could be shown as an error
            # message. This is, however, a show-stopping problem when booting
            # or installing a node. A caller cannot turn the node's power off
            # via the usual methods because they rely on a connection to the
            # cluster. This /could/ generate a preseed that aborts the boot or
            # installation. The caller /could/ mark the node as broken. For
            # now, let the caller make the decision, which might be to retry.
            raise

        # There is no OS-specific preseed data.
        if preseed_type == PRESEED_TYPE.CURTIN:
            return compose_curtin_preseed(node, token, base_url)
        else:
            return compose_cloud_init_preseed(node, token, base_url)
