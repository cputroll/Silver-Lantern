# HeyListen distribution rights record

Recorded 2026-11-18; published as game-channel revision 4 on 2026-11-20.

## Game payload (Sacrappy v0.4.3, 186,632 files)

The payload distributed through the HeyListen launcher and the
`cputroll/Silver-Lantern` game channel is the **official upstream Sacrappy
v0.4.3 build**, repackaged unchanged (ZIP checksum `40d3e2a8...82ba` verified at
build time). Upstream releases the game under the **GNU General Public License
v3**; the verbatim upstream license text is preserved at
`legal/UPSTREAM-GPLv3-LICENSE.txt` (identical to
`https://github.com/Sacrappy/HeyListen/blob/main/LICENSE`). Verbatim
redistribution is authorized by that license; the corresponding source is
publicly available at the upstream repository. The owner's supplementary
distribution license for the launcher packaging is
`legal/HeyListen-Distribution-License.pdf` (document reference
`HL-LIC-v0.4.3-r4`).

## NVIDIA DLSS runtimes (nvngx_dlss.dll, nvngx_dlssg.dll)

The NVIDIA DLSS runtime libraries are obtained via the NGX SDK
(`--fetch-ngx-sdk --accept-nvidia-license` at build time, owner-approved) and
redistributed **incorporated in object code format into the HeyListen
application distribution**, as permitted by the **NVIDIA RTX SDKs License**
(v. January 23, 2023), preserved verbatim at
`legal/NVIDIA-RTX-SDKs-LICENSE.txt` (identical to
`https://github.com/NVIDIA/DLSS/blob/main/LICENSE.txt`). The distribution
satisfies the license's requirements: material additional functionality beyond
the SDK (the HeyListen launcher and update system), protective terms, and
preservation of NVIDIA's notices. NVIDIA, the NVIDIA logo, and DLSS are
trademarks of NVIDIA Corporation.

## Provenance chain

- Official upstream v0.4.3 ZIP: checksum `40d3e2a8...82ba` (full value in the
  build receipt `D:\heylisten-v043-stage`), built in isolation with bundled
  Python and toolchains.
- Packaged revision 4: 21 immutable `r4-whole-*` assets, manifest
  `game-manifest-4.json` SHA-256
  `5d8695a129d914d849b10dc1b1703121c6e972225652d832d088d64d80e52c4f`.
- Offline install proof: `C:\HL043-proof-02` (`OFFLINE_PROOF_OK`, full-SHA
  verification, zero errors).
