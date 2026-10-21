# Code signing policy

Free code signing for ShapeShifter is provided by [SignPath.io](https://signpath.io), with a certificate from the
[SignPath Foundation](https://signpath.org). *(This text applies once the project is accepted; until then the installer is unsigned.)*

## What is signed
- `ShapeShifter-Setup-<version>.exe`, the Windows installer built from this repository by the GitHub Actions workflow
  [`release.yml`](../.github/workflows/release.yml).
- Only builds made by that workflow from a tagged commit on the `main` branch are submitted for signing. Nothing built on
  a developer's PC is signed with the project's certificate.

## Roles
| Role | Who |
|---|---|
| Author, reviewers, approver | Sai Ethihas Chanda ([@SaiEthihasChanda](https://github.com/SaiEthihasChanda)) |

Every signing request is approved manually by the approver after checking that the tag, commit and workflow run match the
release being published.

## Privacy policy
ShapeShifter works on your own computer. It does not collect, store or send personal data, and it has no analytics or
telemetry. Your settings, hotkeys and learned conversions are kept in your Windows user profile. The program contacts the
network only when you ask it to: *Install* buttons in Settings › Engines call `winget`, and *Website* buttons open your
browser. Files you convert are read and written locally. Text recognition uses the recogniser built into Windows.

## Licence and source
ShapeShifter is free software under the GNU Affero General Public License v3.0 or later. The complete source of every release
is in this repository and in the `shapeshifter-<version>-source.zip` attached to each release. Third-party components are listed
in [THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md).

---

# Applying to the SignPath Foundation (maintainer checklist)

Apply at <https://signpath.org/apply>. They ask for the following; answers you can use:

| Question | Answer |
|---|---|
| Project name | ShapeShifter |
| Repository | https://github.com/SaiEthihasChanda/shapeshifter |
| Description | A Windows tool that converts any selected file to another type in place with a radial wheel: images, documents, tables, slides, audio, video, archives, data and more. |
| Licence | GNU AGPL v3 or later (OSI-approved) |
| Artifact to sign | The Inno Setup installer `ShapeShifter-Setup-<version>.exe` |
| Download page | https://github.com/SaiEthihasChanda/shapeshifter/releases |
| Build system | GitHub Actions (`.github/workflows/release.yml`) |
| Maintainers | Sai Ethihas Chanda |
| Code signing policy page | https://github.com/SaiEthihasChanda/shapeshifter/blob/main/docs/CODE-SIGNING.md |

Things they check: the project is public and open-source, has no malware or unwanted software, is actively maintained, and builds
reproducibly in CI. Typical timeline: a few days to a few weeks.

## After you are accepted
1. In SignPath, note your **organization ID**, the **project slug** and the **signing-policy slug**, and create an **API token**.
2. In the GitHub repo: *Settings › Secrets and variables › Actions*: add the secret `SIGNPATH_API_TOKEN`, and the variables
   `SIGNPATH_ORGANIZATION_ID`, `SIGNPATH_PROJECT_SLUG`, `SIGNPATH_SIGNING_POLICY_SLUG`.
3. In SignPath, upload `.signpath/artifact-configuration.xml` as the project's artifact configuration.
4. Push a tag (`git tag v2.5.1 && git push origin v2.5.1`). The workflow builds, submits for signing, waits for your approval
   in SignPath, then attaches the signed installer and checksums to a GitHub release.

The workflow has not been run yet: the first run is likely to need small fixes (it was written without access to GitHub Actions).

## If SignPath says no
Alternatives: Azure Trusted Signing (about $10 a month, identity check), or an OV / EV code-signing certificate from a
certificate authority. For a local certificate, `build.ps1 -Sign -SignCommand '<signtool command with $f>'` signs the exe and the
installer.
