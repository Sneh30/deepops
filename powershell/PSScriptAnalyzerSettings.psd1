# PSScriptAnalyzer policy for OpsPilot Cloud (IMPLEMENTATION.md §3.1, §7).
# Two deliberate exclusions, both documented rather than silently ignored:
#   * PSAvoidUsingWriteHost — installers and the lint gate are console-first and
#     intentionally write human-readable progress to the host.
#   * PSUseBOMForUnicodeEncodedFile — the repository standard is UTF-8 without a
#     BOM so the same files behave identically on macOS authoring hosts and
#     Windows verification hosts.
@{
    IncludeDefaultRules = $true
    Severity            = @('Error', 'Warning')
    ExcludeRules        = @(
        'PSAvoidUsingWriteHost'
        'PSUseBOMForUnicodeEncodedFile'
    )
}
