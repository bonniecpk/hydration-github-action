# Github Actions PoC

This repository contains two PoC Github Actions workflows which perform the following:

1. For every commit to a feature branch:
    * hydrates the template files
1. For every commit to a PR:
    * hydrates the templates files
    * pushes it to the PR as a new commit
    * updates PR metadata with contents of change

These actions pipeline jobs should evolve to also contain validation steps.  They may (and should) be iteratively developed over time.

There are numerous foundational documents which contain concepts used in this repository.  Please review the following as needed:

* [GDC-E Kubernetes Resource Packaging and Hydration Strategy](https://docs.google.com/document/d/1tG3LsA50Gf3frh-SngJqJQH9keqvWfM7qb2gJ94uVyo/edit?tab=t.0#heading=h.17wg41voij6q)
* [GDC-E Config Sync Repo Structure and Workflow](https://docs.google.com/document/d/1JWfGbFlethQLlUAtR3LaZ6oM3nGCMfeg00WjdvouUpM/edit?tab=t.0)
* [GDC-E Source of Truth/CMDB Data Schema](https://docs.google.com/document/d/12Uh_-YwO-5R6-VjgS5ISrZVeD9qya5japFQuCupOQI0/edit?tab=t.0)

## Repo Contents

This repo contains the workflows here:
* [.github/workflows/on_push_to_feature--hydrate_only.yaml](.github/workflows/on_push_to_feature--hydrate_only.yaml)
* [.github/workflows/on_pr.yaml](.github/workflows/on_pr.yaml)

Workflows presume the hydration toolchain ([repo](https://gdc-solutions.git.corp.google.com/enterprise/resource-hyration-cli/)) is local and nested in `cli/`.

_Note_: This isn't the hydration toolchain's home repo - `hydrate.py` is vendored here for demonstration purposes only.  In production, refer to that repo for container building documentation.  Use the container in a Githb Action workflow and modify the actions here to remove the `hydrate.py` setup steps.  That is a future optimization that can be committed here (and in the hydration [repo](https://gdc-solutions.git.corp.google.com/enterprise/resource-hyration-cli/)) whenever it becomes more urgent.

This repository also contains a source of truth, base libraries, and overlays as one would expect in a template repository as described in [GDC-E Kubernetes Resource Packaging and Hydration Strategy](https://docs.google.com/document/d/1tG3LsA50Gf3frh-SngJqJQH9keqvWfM7qb2gJ94uVyo/edit?tab=t.0#heading=h.17wg41voij6q).  This repository serves the use case of a _platform_ template repo in order to demo its GitHub workflows.  The templates here are rendered by the workflows contained here.

## Prerequisites

1. Create a repository on GitHub which and add the following:
    * _GitHub Workflows_ - a copy of the GitHub Action workflows found in this repo
    * _Templates_ - templates to be rendered by the hydration toolchain ([repo](https://gdc-solutions.git.corp.google.com/enterprise/resource-hyration-cli/)) via the GitHub workflows
    * _Hydrated Folder_ - a folder to contain the rendered files

## Proposed Workflow

### Assumptions

* Developers work locally, check in their code to a feature branch, and open PRs to the `main` branch
* Developers must open a PR, receive a review in order to merge to `main`
* Developers are blocked from commiting directly to `main`

### Feature development

Develop code locally and commit to a feature branch.  Push the feature branch to GitHub.  A pipeline will run when the feature branch is pushed.  If successfully hydrated, resources are uploaded as artifacts from the pipeline.

_Note_: While checks do not currently run in the feature branch development flow, they can and should in the future

### PR creation

The developer opens a PR to the `main` branch from their `feature` branch.  Once the code has been merged to `main`, the Pull Request workflow kicks in, running again to hydrate the resources.  Once complete, the pipeline commits the updates to the hydrated files back into the PR.  If additional changes to the PR are required, the developer pulls in the changes committed by the GitHub Actions workflow prior to further updates.

A developer reviews the PR, which contains both the template changes made by the developer, as well as the hydrated artifacts rendered by the GitHub Actions workflow.  The reviewers ensure that the resulting changes align with their expectations by reviewing the propsed PR and comments with their approval: _LGTM_.  A reviewer approves.  The PR is merged.

### Deployment Flow

Once the `main` branch reaches a desired state that is suitable for deployment to an environment, it is tagged with a release tag.

This release tag is referenced in RootSyncs as a `revision`, enabling it to sync up to a precise point-in-time.

