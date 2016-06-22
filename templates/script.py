#!/usr/bin/env python3.5
# Copyright 2012-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""..."""

import argparse

# See http://docs.python.org/release/2.7/library/argparse.html.
argument_parser = argparse.ArgumentParser(description=__doc__)


if __name__ == "__main__":
    args = argument_parser.parse_args()
    print(args)
