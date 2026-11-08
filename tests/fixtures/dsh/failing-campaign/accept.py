#!/usr/bin/env python3
"""A checker that never passes, for adversarial receipt tests."""

import json

print(json.dumps({"verdict": "fail", "diagnostics": "this candidate never passes"}))
