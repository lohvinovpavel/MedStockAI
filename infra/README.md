# `dev` environment — runbook

Provisions the shared GCP sandbox: one GKE cluster, one Cloud SQL Postgres, one
JWT keypair the whole team's tokens verify against.

Design and cost rationale live in [`docs/infra-dev-plan.md`](../docs/infra-dev-plan.md).
This file is just the sequence of commands. Commands are PowerShell — the team
is on Windows.

**Roughly $54/month while it is up.** Costs run whether or not anyone is using
it, and trial credits expire 90 days after signup regardless of balance. See
§9 before leaving it running over a break.

---

## 1. Project and billing — manual, once

Terraform does not create the project; that keeps a mistyped `project_id` from
inventing a new one and silently billing it.

1. Create a project in the console, note its **project ID** (not the name).
2. Link it to the billing account carrying the trial credits.
3. Authenticate locally:

```bash
gcloud auth login
```

```bash
gcloud auth application-default login
```

Both are needed: the first is for the `gcloud` CLI, the second is what
Terraform actually reads.

## 2. State bucket — manual, once

Chicken-and-egg: the backend must exist before `terraform init` can use it.

```bash
gcloud storage buckets create gs://YOUR-PROJECT-ID-tfstate --project=YOUR-PROJECT-ID --location=us-central1 --uniform-bucket-level-access
```

```bash
gcloud storage buckets update gs://YOUR-PROJECT-ID-tfstate --versioning
```

Versioning is not optional. It is the only thing standing between a corrupted
apply and rebuilding the environment by hand.

## 3. Variables

```powershell
cd infra\terraform\dev
copy terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`: set `project_id`, `letsencrypt_email`, and the
`dev_members` emails. `terraform.tfvars` is gitignored — keep it that way.

The Gemini key never goes in a file:

```powershell
$env:TF_VAR_gemini_api_key = "your-real-key"
```

## 4. Apply

```powershell
terraform init -backend-config="bucket=YOUR-PROJECT-ID-tfstate"
```

```powershell
terraform apply
```

Expect **15–20 minutes**. Cloud SQL is the slow part. On a fresh project the
first apply can fail once with an API-not-yet-enabled error even though
`google_project_service` ran — GCP's API enablement is eventually consistent.
**Re-run `terraform apply`; it is idempotent.** Only investigate if it fails
twice in the same place.

## 5. Cluster credentials

```powershell
terraform output -raw get_credentials_command
```

Run what it prints. Then confirm the nodes came up:

```bash
kubectl get nodes
```

## 6. Fill the two placeholders in the overlay

The overlay ships placeholders because the hostname is derived from an IP
Terraform only knows after apply.

```powershell
$host_name = terraform output -raw ingress_host
$repo = terraform output -raw artifact_registry_repo
$email = terraform output -raw letsencrypt_email
cd ..\..\..
(Get-Content deploy\overlays\dev\kustomization.yaml) -replace 'medstock-dev\.REPLACE-ME\.sslip\.io', $host_name -replace 'REPLACE-ME-REGISTRY', $repo | Set-Content deploy\overlays\dev\kustomization.yaml
(Get-Content deploy\overlays\dev\cluster-issuer.yaml) -replace 'REPLACE-ME@example\.com', $email | Set-Content deploy\overlays\dev\cluster-issuer.yaml
```

Check it before applying — this is a local edit you are expected to commit
once, not on every apply:

```bash
kubectl kustomize deploy/overlays/dev | Select-String "host:|newName:|email:"
```

## 7. Build and push images

The manifests point at Artifact Registry; nothing is there yet.

```powershell
gcloud auth configure-docker us-central1-docker.pkg.dev
```

```powershell
foreach ($s in "auth","inventory","analogue","compliance","patient-profiling","prediction","warehouse","ingest") { docker build --build-arg SERVICE=$s -t "$repo/${s}:latest" . ; docker push "$repo/${s}:latest" }
```

`web` builds from `web/` with its own Dockerfile, not this one.

> Docker is used **here only**, to build images for the cloud. Local
> development still runs natively — see `docs/auth/auth-integration.md`.

## 8. Deploy

```bash
kubectl apply -k deploy/overlays/dev
```

Then run the migration before anything expects a schema:

```bash
kubectl apply -f deploy/k8s/migrate-job.yaml -n medstock
```

```bash
kubectl wait --for=condition=complete job/migrate -n medstock --timeout=300s
```

Seed the first hospital and users:

```bash
kubectl run seed --rm -it --restart=Never -n medstock --image=$repo/auth:latest --env="SEED_PASSWORD=pick-something" --command -- python -m app.seed
```

Watch the certificate go valid (**2–5 minutes**; until it does, the browser
shows a self-signed warning and login will not work because the session cookie
is `Secure`):

```bash
kubectl get certificate -n medstock -w
```

Then open `https://<ingress_host>`.

## 9. Cost control

| Item | $/month |
|---|---|
| GKE control plane, 1 zonal cluster | 0 (free-tier credit) |
| 2× e2-medium Spot | ~15 |
| 2× 20GB pd-balanced boot disks | ~4 |
| Cloud SQL db-f1-micro + 10GB HDD + backups | ~9 |
| Cloud SQL public IPv4 | ~7 |
| Network LB forwarding rule | ~18 |
| Artifact Registry / Secret Manager / GCS state | <1 |
| **Total** | **~$54** |

Going away for a while:

```powershell
terraform destroy
```

Everything is reproducible. Only the database contents are lost, and `seed.py`
rebuilds those. **The JWT keypair is regenerated on the next apply**, so every
existing token stops verifying — expected, everyone logs in again.

Scaling the node pool to 0 saves only ~$19/mo; the load balancer and Cloud SQL
keep billing. `destroy` is the real lever.

## 10. Connecting to the database from your laptop

Do not put the DB password in your shell history — it is in Secret Manager.
Use the Cloud SQL Auth Proxy, which authenticates with your own Google identity
(this is why you were granted `roles/cloudsql.client`):

```powershell
cloud-sql-proxy --port 5432 $(terraform output -raw cloudsql_connection_name)
```

Then in another terminal, with the password from:

```powershell
gcloud secrets versions access latest --secret=medstock-dev-db-url
```

connect to `localhost:5432`, database `medstock`, user `medstock`.

**This is the shared dev database.** RLS policies do not exist yet
(`docs/services.md` §8 #2), so nothing stops one service's query from reading
another hospital's rows. Do not treat this environment as proof of tenant
isolation.

## 11. When something is broken

| Symptom | Cause |
|---|---|
| Every `/api/*` call 404s | The GCE ingress controller claimed the Ingress. `kubectl get ingress -n medstock` must show class `nginx`, not `gce`. |
| Ingress creation hangs or times out on a webhook | ingress-nginx admission webhook unreachable from the control plane. Check the `ingress-nginx-admission` ValidatingWebhookConfiguration. |
| Certificate stuck `False` for >10 min | HTTP-01 needs port 80 reachable. `ssl-redirect` must not break the ACME challenge path — check `kubectl describe challenge -n medstock`. |
| Login returns 200 but `/me` then 401 | The cookie is `Secure`; you are on `http://`. Use the https URL. |
| Pods `Pending` | Spot capacity. The pool autoscales to 3; if GCP has no Spot e2-medium, wait or temporarily set `spot = false`. |
| `terraform apply` fails on an API | Eventually-consistent enablement. Re-run once. |
