# Copyright 2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Functionality to refresh rack controller hardware and networking details."""
import os
import socket
import stat
import subprocess
import tempfile
import urllib

from provisioningserver.logger import get_maas_logger
from provisioningserver.refresh.maas_api_helper import (
    encode_multipart_data,
    geturl,
)
from provisioningserver.refresh.node_info_scripts import NODE_INFO_SCRIPTS
from provisioningserver.utils.network import get_all_interfaces_definition
from provisioningserver.utils.shell import (
    call_and_check,
    ExternalProcessError,
)
from provisioningserver.utils.twisted import synchronous


maaslog = get_maas_logger("refresh")


def get_architecture():
    """Get the architecture of the running system."""
    try:
        stdout = call_and_check('archdetect').decode('utf-8')
    except ExternalProcessError:
        return ''
    arch, subarch = stdout.strip().split('/')
    if arch in ['i386', 'amd64', 'arm64', 'ppc64el']:
        subarch = 'generic'
    return '%s/%s' % (arch, subarch)


def get_os_release():
    """Parse the contents of /etc/os-release into a dictionary."""
    def full_strip(value):
        return value.strip().strip('\'"')

    os_release = {}
    with open('/etc/os-release') as f:
        for line in f:
            key, value = line.split('=')
            os_release[full_strip(key)] = full_strip(value)

    return os_release


@synchronous
def get_sys_info():
    """Return basic system information in a dictionary."""
    os_release = get_os_release()
    if 'ID' in os_release:
        osystem = os_release['ID']
    elif 'NAME' in os_release:
        osystem = os_release['NAME']
    else:
        osystem = ''
    if 'UBUNTU_CODENAME' in os_release:
        distro_series = os_release['UBUNTU_CODENAME']
    elif 'VERSION_ID' in os_release:
        distro_series = os_release['VERSION_ID']
    else:
        distro_series = ''
    return {
        'hostname': socket.gethostname().split('.')[0],
        'architecture': get_architecture(),
        'osystem': osystem,
        'distro_series': distro_series,
        'interfaces': get_all_interfaces_definition(),
    }


def signal(
        url, creds, status, message, files: dict=None, script_result=None,
        extra_headers=None):
    """Send a node signal to a given maas_url."""
    if isinstance(status, int):
        status = str(status)
    params = {
        b"op": b"signal",
        b"status": status.encode("utf-8"),
        b"error": message.encode("utf-8"),
    }
    if script_result is not None:
        if isinstance(script_result, int):
            script_result = str(script_result)
        params[b'script_result'] = script_result.encode("utf-8")

    data, headers = encode_multipart_data(
        params, ({} if files is None else files))

    if extra_headers is not None:
        headers.update(extra_headers)
    try:
        payload = geturl(url, creds=creds, headers=headers, data=data)
        if payload != b"OK":
            maaslog.error(
                "Unexpected result sending region commissioning data: %s" % (
                    payload))
    except urllib.error.HTTPError as exc:
        maaslog.error("http error [%s]" % exc.code)
    except urllib.error.URLError as exc:
        maaslog.error("url error [%s]" % exc.reason)
    except socket.timeout as exc:
        maaslog.error("socket timeout [%s]" % exc)
    except TypeError as exc:
        maaslog.error(str(exc))
    except Exception as exc:
        maaslog.error("unexpected error [%s]" % exc)


@synchronous
def refresh(system_id, consumer_key, token_key, token_secret, maas_url=None):
    """Run all builtin commissioning scripts and report results to region."""
    maaslog.info(
        "Refreshing rack controller hardware information.")

    url = "%s/metadata/status/%s/latest" % (maas_url, system_id)

    creds = {
        'consumer_key': consumer_key,
        'token_key': token_key,
        'token_secret': token_secret,
        'consumer_secret': '',
    }
    scripts = {
        name: config
        for name, config in NODE_INFO_SCRIPTS.items()
        if config["run_on_controller"]
    }

    with tempfile.TemporaryDirectory(prefix='maas-commission-') as tmpdir:
        failed_scripts = runscripts(scripts, url, creds, tmpdir=tmpdir)

    if len(failed_scripts) == 0:
        signal(url, creds, "OK", "Finished refreshing %s" % system_id)
    else:
        signal(url, creds, "FAILED", "Failed refreshing %s" % system_id)


def runscripts(scripts, url, creds, tmpdir):
    total_scripts = len(scripts)
    current_script = 1
    failed_scripts = []
    for output_name, config in scripts.items():
        signal(
            url, creds, "WORKING", "Starting %s [%d/%d]" %
            (config['name'], current_script, total_scripts))

        # Write script to /tmp and set it executable
        script_path = os.path.join(tmpdir, config['name'])
        with open(script_path, 'wb') as f:
            f.write(config['content'])
        st = os.stat(script_path)
        os.chmod(script_path, st.st_mode | stat.S_IEXEC)

        # Execute script and store stdout/stderr
        proc = subprocess.Popen(
            script_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = proc.communicate()
        signal(
            url, creds,
            "WORKING", "Finished %s [%d/%d]: %d" %
            (config['name'], current_script, total_scripts, proc.returncode),
            {output_name: stdout, "%s.err" % config['name']: stderr},
            proc.returncode)
        if proc.returncode != 0:
            failed_scripts.append(config['name'])
        current_script += 1
    return failed_scripts
