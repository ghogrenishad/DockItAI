# Pilot deployment runbook

**Status:** active pilot runbook — update this document as the deployment changes.

This runbook deploys DockIt as a private, low-cost EC2 pilot. It is not a
production high-availability design. The current deployment is accessed only
through AWS Systems Manager (SSM); the API and Streamlit UI do not have public
inbound ports.

## Deployment shape

- One Amazon Linux 2023 x86_64 EC2 instance.
- A `t3.micro` instance is sufficient for smoke tests and a small pilot. Use
  standard CPU credits to avoid surplus-credit charges.
- One encrypted 20 GiB `gp3` EBS root volume. Do not attach EFS or FSx while
  SQLite remains the authoritative pilot store.
- FastAPI runs as the `dockit-api` systemd service on `127.0.0.1:8000`.
- Streamlit runs as the `dockit-ui` systemd service on `127.0.0.1:8501`.
- SSM Session Manager supplies administrator shell access and browser port
  forwarding. SSH and application ports remain closed in the security group.

## Cost and security guardrails

- Keep the instance, EBS, Cognito, and any later services within the AWS Free
  Tier or approved credits. Check the AWS console for current eligibility
  before creating a resource.
- Do not create an Application Load Balancer, NAT Gateway, Elastic IP, EFS,
  FSx, managed Redis, or AgentCore resources for this phase.
- Use an EC2 instance profile with `AmazonSSMManagedInstanceCore`. Prefer SSM
  over SSH and do not store AWS keys, JWTs, database copies, or personal email
  addresses in this repository.
- Use Secrets Manager or a similarly approved secret store before adding any
  non-pilot API key or credential.

## Backup and recovery

- The pilot EBS volume has the default EBS snapshot policy enabled with a
  daily schedule and three-day retention. This protects the host, SQLite pilot
  database, and deployed working tree from loss of the attached volume.
- The application also creates an on-volume SQLite backup before a deliberate
  pilot identity-data update. That backup helps reverse an operator mistake but
  is not a substitute for the EBS snapshots.
- A restore drill remains required before treating this as a releasable pilot:
  restore a snapshot to a new volume or instance, validate `/healthz` and
  `/readyz`, and confirm the intended pilot data is present.

## EC2 host preparation

1. Launch Amazon Linux 2023 x86_64 in the intended AWS Region.
2. Attach the EC2 role that grants `AmazonSSMManagedInstanceCore` and confirm
   Session Manager connectivity.
3. Install the runtime tools:

   ```bash
   sudo dnf upgrade -y
   sudo dnf install -y git python3.14 python3.14-pip sqlite
   ```

4. Add one GiB of swap on the pilot EBS volume. This prevents dependency
   installation or scheduler work from exhausting a `t3.micro` host:

   ```bash
   sudo fallocate -l 1G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   sudo swapon -a
   ```

5. Clone the repository under `/opt/dockit/app`, create a Python 3.14 virtual
   environment, and install the application and pilot dependencies:

   ```bash
   sudo install -d -o "$(id -un)" -g "$(id -gn)" -m 0755 /opt/dockit
   git clone <repository-url> /opt/dockit/app
   cd /opt/dockit/app
   python3.14 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -e '.[dev,ui]'
   pytest -q
   ruff check .
   mypy src
   ```

## Pilot database and local smoke test

The tracked schema seed creates a disposable pilot database. Do not use this
procedure for a production SetuHaul database without data-owner approval.

```bash
cd /opt/dockit/app
test ! -e data/dockit.db
sqlite3 data/dockit.db < data/setuhaul_schema_and_seed.sql
test ! -e .env
cp .env.example .env
```

For a first smoke test, start the API locally and verify:

```bash
nohup .venv/bin/dockit-api > /tmp/dockit-api.log 2>&1 &
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
curl -fsS http://127.0.0.1:8000/api/v1
```

## Persistent services

Create a non-login `dockit` system user, give it ownership of `/opt/dockit`,
and run both services through systemd. The service units use restart-on-failure,
loopback binds, and systemd filesystem hardening.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dockit-api dockit-ui
sudo systemctl status dockit-api dockit-ui --no-pager
```

Operational checks:

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
curl -fsSI http://127.0.0.1:8501
sudo journalctl -u dockit-api -n 80 --no-pager
sudo journalctl -u dockit-ui -n 80 --no-pager
```

The Streamlit module must have an executable module guard so `streamlit run`
actually invokes `run()`:

```python
if __name__ == "__main__":
    run()
```

Keep this change committed in the repository before future host updates.

## Private browser access

Install the Session Manager plugin on the administrator workstation. A
user-directory installation is sufficient when administrator privileges are
unavailable. Authenticate the AWS CLI with a short-lived profile, then forward
the private Streamlit port:

```bash
aws ssm start-session \
  --profile <profile> \
  --region <region> \
  --target <instance-id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8501"],"localPortNumber":["8501"]}'
```

Keep that terminal open and browse to `http://127.0.0.1:8501`. This does not
need an inbound security-group rule.

## Pilot identity checkpoint

The current Streamlit UI accepts a manually supplied Cognito access token. A
real stakeholder pilot therefore needs all of the following before protected
API journeys can work:

The Cognito Lite User Pool and public pilot app client have been created in the
deployment Region. Their identifiers are deployment configuration and are kept
outside this runbook. Cognito group membership, pilot-user creation, driver
enrollment, and application configuration remain pending.

1. A Cognito Lite User Pool in the deployment Region.
2. A public app client without a client secret, configured for the pilot's
   selected authentication flow.
3. A `drivers` Cognito group and approved pilot users.
4. A consented, real pilot email mapped to each selected source driver in the
   pilot database.
5. A controlled mapping from the immutable Cognito `sub` to the driver ID in
   `driver_identity_links`.

Use the local, administrator-only `dockit-enroll-driver` command for the final
mapping. It prompts for the approved email, resolves the associated driver,
enforces one subject and one driver per link, and appends an audit event. Run it
through SSM on the pilot host after the Cognito user's `sub` is known:

```bash
sudo -u dockit /opt/dockit/app/.venv/bin/dockit-enroll-driver --subject <cognito-sub>
```

It prints only the linked driver ID. Do not use direct `INSERT` statements for
`driver_identity_links`.

Do not use test-only `.test` addresses for a stakeholder who must receive an
invitation or complete email-based account recovery. Do not record email
addresses, access tokens, user-pool IDs, client IDs, instance IDs, account
numbers, or database contents in this runbook.

The current manual-token UI is adequate for a controlled pilot. A future
stakeholder-facing login page requires OAuth authorization-code flow with PKCE,
plus a stable HTTPS callback URL and corresponding deployment controls.

## Next steps

1. Complete Cognito Lite configuration and create the approved pilot users.
2. Add a controlled driver-enrollment/bootstrap procedure; do not hand-edit
   `driver_identity_links` in an untracked manner.
3. Configure application settings with the non-secret Cognito identifiers and
   restart the API.
4. Test one driver-owned API journey over the private SSM tunnel.
5. Before a broader release, add telemetry, backups, restore testing, alerting,
   retention policy, a deployment rollback procedure, and approved secret
   management as required by Step 9.
