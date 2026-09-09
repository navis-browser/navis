# Licensing and source distribution

Navis-owned software in the Navis, Runtime and Platform repositories uses the unmodified Mozilla Public License 2.0 (MPL-2.0), unless a file states otherwise. Repository documentation and Navis-authored source assets use the same license for simplicity. Third-party source, archives, translations, fonts, rules and images retain their original licenses and notices. A repository-wide default does not relicense them. Keep upstream headers; add `SPDX-License-Identifier: MPL-2.0` to new Navis-owned files where comments are supported. Files that cannot carry comments and generated assets inherit the applicable repository notice; do not edit signed archives or generated outputs just to insert a header.

MPL obligations apply to covered source files and modifications, not automatically to every independent file in a larger application. Commercial use and embedding are allowed. When distributing covered executables, provide the corresponding covered source and tell recipients how to obtain it. Private changes do not create a requirement to submit a pull request. Do not add restrictions to the MPL text or mark the project as incompatible with secondary licenses without a separate licensing decision.

## Source and binary releases

For each binary release, publish clear source directions beside the download and include them with the distribution. Identify the exact Navis/Runtime/Platform commits, the pinned Gecko revision, the complete semantic-port series, build scripts and configuration needed for the covered source. Offer downloadable matching source; a moving branch, an empty repository or a link to license terms is not corresponding source. The desktop Runtime API archive is not the complete corresponding source of the Android application or the full Gecko binary.

For uBlock Origin, preserve the original GPL-3.0-or-later license and all component notices. Identify the exact extension source commit and provide corresponding source with its build scripts and required subdependencies. GPL section 6(d) permits another server to host source when equivalent access and clear directions are provided beside the object-code download; the distributor remains responsible for availability. Do not assume an arbitrary upstream archive includes every required dependency, or that downloading a signed extension grants permission to omit source directions.

Before public binary distribution, verify the complete resolved dependency set, native libraries, dictionaries, filter data and bundled extensions against their applicable license/NOTICE requirements. Include the required notices offline. Android's generated dependency report covers the selected Gradle runtime artifacts, not Gecko's native dependency closure. A source package check is not a legal certification of a binary, and existing test binaries do not acquire new notices merely because repository documentation changed.

## Brand and contributions

MPL-2.0 section 2.3 does not grant rights to contributors' trademarks, service marks or logos except as needed for license notices. Referring accurately to Navis is distinct from presenting a modified build as an official Navis release. The Platform trademark notice does not restrict rights granted by the software licenses, claim trademark registration, or override lawful descriptive use.

Submit contributions under the existing license of the affected files and retain third-party attribution. A copyright line identifying a contributor does not replace upstream ownership or transfer copyright. This project does not require a copyright assignment or separate CLA merely through this document.

Authoritative terms and explanations:

- https://www.mozilla.org/MPL/2.0/
- https://www.mozilla.org/en-US/MPL/2.0/FAQ/
- https://www.gnu.org/licenses/gpl-3.0.html#section6
- https://www.apache.org/licenses/LICENSE-2.0
