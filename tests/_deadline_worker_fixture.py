"""Offline child for process-deadline tests; never opens a network connection."""

import json
import os
import sys
import time


def main():
    json.load(sys.stdin)
    mode = os.environ.get("COMMANDER_GYM_TEST_WORKER_MODE")
    if mode == "success":
        print(json.dumps({"kind": "response", "response": {
            "id": "resp-test", "status": "completed",
            "output_text": '{"channel":"action","semanticId":"argentum-action-v1:pass","params":{}}',
            "usage": {"input_tokens": 100, "output_tokens": 20,
                      "input_tokens_details": {"cached_tokens": 0,
                                               "cache_write_tokens": 100}},
        }}))
    elif mode == "server520":
        print(json.dumps({"kind": "error", "status_code": 520, "code": "upstream_timeout"}))
    elif mode == "blocked":
        time.sleep(5)
    else:
        # Continuous progress defeats an inactivity timeout but must not
        # extend the whole-request wall deadline.
        while True:
            sys.stdout.write(" ")
            sys.stdout.flush()
            time.sleep(0.08)


if __name__ == "__main__":
    main()
