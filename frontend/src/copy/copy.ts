export const Copy = {
  productName: "Job Signal",
  productNameSubtitle: "local-first job intelligence",
  footer:
    "Job Signal (JobSignal) — local-first job intelligence. This build never submits applications.",
  previewBanner:
    "Preview mode: the desktop worker is not connected, so the values below are sample data.",
  nav: {
    today: "Today",
    dashboard: "Today",
    discover: "Find jobs",
    searchProfiles: "Search Profiles",
    saved: "Saved",
    applications: "Applications",
    companies: "Companies",
    postingIntelligence: "Posting intelligence",
    keywords: "Skills",
    resume: "Profile",
    settings: "Settings",
    diagnostics: "Diagnostics",
    seen: "History",
  },
  navDescriptions: {
    today: "Your search plan and the matches to look at next.",
    dashboard: "Your search plan and the matches to look at next.",
    discover: "Best matches and a full search of the jobs already collected.",
    searchProfiles: "Saved searches now live on Find jobs.",
    saved: "Roles you shortlisted, ready to apply to.",
    applications: "What you have applied to, outcomes, and what is hidden.",
    companies: "Company history and hiring activity, including merged employers.",
    postingIntelligence: "Reposts, stale postings and posting quality signals.",
    keywords: "Skill demand across postings and how your profile covers it.",
    resume: "Resume, preferences and matching.",
    settings: "Sources, backup, application answers, plugins and flags.",
    diagnostics: "Worker health, tasks and recent events.",
    seen: "Postings that disappeared, were linked as duplicates, or are hidden.",
  },
  status: {
    idle: "Not connected",
    connecting: "Connecting…",
    ready: "Connected",
    error: "Connection failed",
    preview: "Preview data",
  },
  home: {
    title: "Today",
    subtitle: "Your job search, one step at a time",
    capabilities: "What this build can do",
    welcome:
      "The worker finds career sites, crawls the boards that matter most, links reposts and duplicates, matches jobs against your resume and shows which source each posting came from. Everything stays on this computer.",
    health: "Worker health",
    version: "App version",
  },
  settings: {
    title: "Settings",
    description: "Where data is stored, which optional features are enabled and which plugins are registered.",
    general: "General",
    featureFlags: "Feature flags",
    plugins: "Plugins",
    data: "Data",
    dataDir: "Data directory",
    database: "Database file",
    appVersion: "App version",
    protocolVersion: "Protocol version",
    schemaVersion: "Database schema version",
    settingsFormatVersion: "Settings format version",
    pluginId: "Plugin",
    pluginKind: "Kind",
    pluginVersion: "Version",
    pluginHealth: "Health",
    pluginEnabled: "Enabled",
    pluginHosts: "Allowed network hosts",
    pluginNone: "No plugins are registered.",
    none: "Not reported",
    enable: "Enable",
    disable: "Disable",
    loadError: "Could not load settings.",
    backup: "Backup and restore",
    exportBackup: "Export a backup",
    importBackup: "Restore from a backup",
    importPath: "Backup file (inside the data directory)",
    importConfirm:
      "I understand a restore only fills an empty database and cannot overwrite existing data.",
    backupHistory: "Backup history",
    noBackups: "No backups exported yet.",
    overrides: "Stored configuration overrides",
    noOverrides: "No overrides stored; built-in defaults are in use.",
  },
  diagnostics: {
    title: "Diagnostics",
    description: "Worker health, running tasks and recent events, for troubleshooting.",
    health: "Health",
    status: "Status",
    queueDepth: "Tasks waiting",
    diskUsage: "Disk used",
    pluginHealth: "Plugin health",
    recentTasks: "Recent tasks",
    noTasks: "No tasks have run yet. Use “Run test task” to check progress and cancellation.",
    startTask: "Run test task",
    cancel: "Cancel",
    exportLogs: "Export logs",
    exportLogsPlaceholder:
      "Log export is not available yet. Log files are written to the logs folder inside the data directory.",
    taskId: "Task",
    taskType: "Type",
    taskStatus: "Status",
    taskProgress: "Progress",
    taskActions: "Actions",
    loadError: "Could not load diagnostics.",
    healthError: "Could not load worker health.",
    events: "Recent worker events",
    noEvents: "No events received yet.",
  },
  placeholders: {
    discover: {
      title: "Discover Jobs",
      body: "Add a career page or job board URL, crawl it, and review the postings found.",
    },
    searchProfiles: {
      title: "Search Profiles",
      body: "Search your job database and save reusable hard filters.",
    },
    saved: {
      title: "Saved",
      body: "Roles you shortlisted, ready to apply to.",
    },
    applications: {
      title: "Applications",
      body: "Track what you applied to and review the roles hidden because of it.",
    },
    companies: {
      title: "Companies",
      body: "Company history and hiring activity, including merged employers.",
    },
    postingIntelligence: {
      title: "Posting intelligence",
      body: "Reposts, stale postings and posting quality signals.",
    },
    keywords: {
      title: "Skills",
      body: "Skill demand across postings and how your profile covers it.",
    },
    resume: {
      title: "Resume Manager",
      body: "Import a resume, edit your profile and run job matching.",
    },
    seen: {
      title: "Previously Seen",
      body: "Postings that disappeared and jobs linked as duplicates or reposts, with a way to separate them again.",
    },
    job: {
      title: "Job detail",
      body: "Open a job from Discover Jobs or Previously Seen to read its history.",
    },
  },
  emptyState: {
    heading: "Screen not found",
    body: "That address does not match a screen. Use the sidebar to return to Today or Find jobs.",
  },
  tooltips: {
    status:
      "The worker is the background program on this computer that crawls job boards and stores the results. It never uploads your data.",
    health: "Overall state reported by the local worker.",
    schema:
      "Version of the local database layout. It is upgraded automatically when the app updates.",
    settingsSchema:
      "Version of the stored settings format. This is separate from the database schema version.",
    sources:
      "A source is a career page or job board the worker crawls. Paste the URL of a company's job list, not an individual posting.",
    runSource:
      "Crawl this source now. New postings are added, changed postings get a new snapshot, and postings that disappeared are marked as removed.",
    tier:
      "Discovery ranking. Hot sources recently produced relevant jobs, warm sources are promising, and cold sources are low priority.",
    viaIndex:
      "Found through the local index of known career sites, rather than by crawling a source you added.",
    extractedBy: "The extraction strategies that read this posting, in the order they ran.",
    autoDiscovery:
      "Lets the worker find new career sites by itself and re-crawl the ones that are due. It runs in the background in bounded passes.",
    profile:
      "A named set of hard filters. A profile does not change your resume; it narrows and ranks the jobs shown here.",
    hardFilters:
      "Jobs that fail these filters are hidden from results. The same filters rank discovery sources into hot, warm and cold tiers.",
    saveFilters:
      "Saves these filters to the selected profile. Searches use the saved filters, so save before running a search.",
    results: "Jobs from your local database, with the selected profile's filters applied.",
    seen:
      "Jobs that disappeared from their source, plus jobs linked as duplicates or reposts.",
    relatedJob: "The job this posting was automatically linked to as a duplicate or repost.",
    mergeCompany:
      "Merging treats two company names as one employer. The decision is recorded and can be undone; nothing about the postings themselves is changed.",
    mergeAudit:
      "The merge audit trail is append-only. A separation is recorded as a new decision rather than removing the old one.",
    keywordsHow:
      "Prevalence counts the share of postings that mention a skill. Below the small-sample threshold only counts are shown, and a posting with no readable text is missing evidence rather than a zero.",
    unmerge:
      "Undo the duplicate link so both jobs stay separate. This decision is remembered; automation will not link them again.",
    importResume:
      "Imports a PDF or text resume as a new version. The file is read locally and never modified or uploaded.",
    versionLabel: "A name for this import, so you can tell versions apart.",
    activeVersion:
      "Matching uses only the active version's parsed text, skills and profile overrides.",
    overrides:
      "Your edits are stored separately from the imported file, so importing again does not lose them.",
    skillStatus:
      "Tell the matcher what you actually know. “Not relevant” removes a skill from scoring; “Have experience” counts it even when the parser missed it.",
    preferences:
      "Used by the location and compensation parts of the fit score. Lists are comma-separated.",
    weights:
      "How much each dimension contributes to the fit score. Saving normalizes the weights to 100% and recomputes matches.",
    fitScore:
      "A weighted average of the dimensions that have data. It is a ranking heuristic, not a probability of getting an interview.",
    coverage:
      "Share of the fit-score dimensions that had enough data. Below 60% the score is less reliable.",
    limitedData: "Too many scoring dimensions are missing for this score to be reliable.",
    matchResults: "Latest matching run for the active resume version.",
    snapshots:
      "Each materially different version of a posting is stored as a snapshot, so you can see what changed over time.",
    reposts:
      "Links to the same job at another URL or ATS id. “Posted date reset” means the posting date moved later while the text stayed identical.",
    observations:
      "How many times the worker has seen this posting, including its first discovery.",
    relations: "Other jobs linked to this one, including manual merges and separates.",
    featureFlags:
      "Optional capabilities compiled into this build. They are read-only here and are changed in the app configuration.",
    crawlInterval:
      "How often the worker revisits this source. Set 0 to stop scheduled crawls.",
    plugins:
      "Extraction and discovery plugins the worker may use, including the network hosts each is allowed to contact.",
    queueDepth: "Number of tasks waiting to run in the local worker.",
    diskUsage: "Space used by the local job database and logs.",
    backup:
      "A backup is a portable JSON file of your local data. A restore only fills an empty database, so nothing you already have is overwritten.",
    testTask:
      "Starts a short no-op task so you can check progress reporting and cancellation.",
    exportLogs:
      "Log export is not available yet. Find the log files in the logs folder inside the data directory.",
    events: "Live progress events from the worker, newest first. The ten most recent are kept.",
    taskProgress: "Current step and total steps, as reported by the worker.",
  },
} as const;

export type PlaceholderKey = keyof typeof Copy.placeholders;
