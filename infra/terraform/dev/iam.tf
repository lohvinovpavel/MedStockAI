# Seven people need to `kubectl` and reach the DB; granting it here beats
# seven console clicks nobody remembers doing.
locals {
  dev_member_roles = [
    "roles/container.developer",
    "roles/cloudsql.client",
    "roles/secretmanager.secretAccessor",
  ]

  dev_member_role_pairs = {
    for pair in setproduct(var.dev_members, local.dev_member_roles) :
    "${pair[0]}|${pair[1]}" => { member = pair[0], role = pair[1] }
  }
}

resource "google_project_iam_member" "dev_members" {
  for_each = local.dev_member_role_pairs

  project = var.project_id
  role    = each.value.role
  member  = "user:${each.value.member}"
}
