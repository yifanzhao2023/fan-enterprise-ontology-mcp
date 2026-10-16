#!/usr/bin/env bash
# PR gate for the derived layer. Fails the build if the ontology, the rules
# or the sample ABox stopped agreeing with each other.
set -euo pipefail

make lint-projection
make unit
make demo
