import os

# Request-boundary enforcement is exercised with focused tests. The in-process
# TestClient does not enter through Caddy.
os.environ["TALLYSTEAD_NETWORK_ENFORCEMENT_ENABLED"] = "false"
