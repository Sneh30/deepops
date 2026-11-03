@{
    RootModule        = ''
    ModuleVersion     = '1.0.0'
    GUID              = 'f7f1a0f4-1f8e-4a0b-9a7b-2f5f0d6c9e11'
    Author            = 'OpsPilot'
    CompanyName       = 'OpsPilot'
    Copyright         = '(c) OpsPilot'
    Description       = 'Pinned PowerShell tooling dependencies for OpsPilot Cloud.'
    PowerShellVersion = '7.2'

    RequiredModules   = @(
        @{
            ModuleName    = 'PSScriptAnalyzer'
            ModuleVersion = '1.24.0'
            MaximumVersion = '1.24.999'
        }
    )
}
