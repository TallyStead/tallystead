import os

# Request-boundary enforcement and the live Caddy controller are exercised with
# focused tests. The in-process TestClient does not enter through Caddy.
os.environ["TALLYSTEAD_NETWORK_ENFORCEMENT_ENABLED"] = "false"
os.environ["TALLYSTEAD_NETWORK_CONTROLLER_ENABLED"] = "false"
