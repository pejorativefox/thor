# -*- coding: utf-8 -*-
"""`python -m thor` entry."""

from .app import main
import sys

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
