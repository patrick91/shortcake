import {
  FileDiff,
  type FileDiffProps,
  type DiffLineAnnotation,
  type AnnotationSide,
  type SelectedLineRange,
  WorkerPoolContextProvider,
} from '@pierre/diffs/react';
import { getFiletypeFromFileName, getSingularPatch, setLanguageOverride } from '@pierre/diffs';
import { preloadDiffHTML } from '@pierre/diffs/ssr';
import DiffsWorker from '@pierre/diffs/worker/worker.js?worker';
import type { FileTreeRowDecoration, GitStatusEntry } from '@pierre/trees';
import { FileTree as PierreFileTree, useFileTree } from '@pierre/trees/react';
import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { Popover } from '@base-ui-components/react/popover';

type DiffStyle = 'unified' | 'split';
type ThemeMode = 'dark' | 'light' | 'system';

type DiffComment = {
  id: string;
  file: string;
  startLine: number;
  endLine: number;
  side: AnnotationSide;
  text: string;
  source?: { type: 'ai'; model: string; severity: string };
};

type ReviewModel = {
  id: string;
  name: string;
  tool: string;
  available: boolean;
};

type CommentMeta = {
  commentId: string;
  text: string;
  isInput: boolean;
  isToolbar?: boolean;
  isSplitSelection?: boolean;
  splitSelectionId?: string;
};

type SplitLinesResponse = {
  sourceBranch: string;
  newBranches: string[];
  restackedBranches: string[];
};

type SplitLineSelection = {
  id: string;
  file: string;
  startLine: number;
  endLine: number;
  side: AnnotationSide;
  filePatch: string;
};

type ActiveInput = {
  file: string;
  startLine: number;
  endLine: number;
  side: AnnotationSide;
} | null;

function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (cb: () => void) => {
      const mql = window.matchMedia(query);
      mql.addEventListener('change', cb);
      return () => mql.removeEventListener('change', cb);
    },
    [query],
  );
  const getSnapshot = useCallback(() => window.matchMedia(query).matches, [query]);
  return useSyncExternalStore(subscribe, getSnapshot, () => true);
}

function formatLineRef(file: string, startLine: number, endLine: number): string {
  return startLine === endLine ? `${file}:${startLine}` : `${file}:${startLine}-${endLine}`;
}

function formatLineLabel(startLine: number, endLine: number): string {
  return startLine === endLine ? `Line ${startLine}` : `Lines ${startLine}-${endLine}`;
}

type StackBranch = {
  name: string;
  parent: string;
  depth: number;
  isCurrent: boolean;
  commitCount: number;
  commit: string;
  commitShort: string;
  commitSubject: string;
};

type StackResponse = {
  currentBranch: string | null;
  branches: StackBranch[];
};

type DiffResponse = {
  branch: string;
  parent: string;
  patch: string;
};

type WorkingDiffResponse = {
  patch: string;
};

type UIStateResponse = StackResponse & {
  workingDiffKey: string;
};

type GitHubBranchInfo = {
  prNumber: number | null;
  prUrl: string | null;
  prIsDraft: boolean;
  checkStatus: 'success' | 'failure' | 'pending' | null;
};

type GitHubInfoResponse = {
  branches: Record<string, GitHubBranchInfo>;
};

type DiffSelection =
  | { type: 'branch'; name: string }
  | { type: 'working' };

function selectionFromHash(hash: string): DiffSelection | null {
  const path = hash.replace(/^#\/?/, '');
  if (path === 'working') return { type: 'working' };
  if (path.startsWith('branch/')) {
    const name = decodeURIComponent(path.slice('branch/'.length));
    if (name) return { type: 'branch', name };
  }
  return null;
}

function selectionToHash(sel: DiffSelection): string {
  if (sel.type === 'working') return '#/working';
  return `#/branch/${encodeURIComponent(sel.name)}`;
}

function stackPollKey(stack: StackResponse): string {
  return JSON.stringify({
    currentBranch: stack.currentBranch,
    branches: stack.branches,
  });
}

type FileInfo = {
  path: string;
  name: string;
  additions: number;
  deletions: number;
  status: GitStatusEntry['status'];
  patchIndex: number;
};

const API_BASE = import.meta.env.VITE_SHORTCAKE_API_URL ?? '';
const FILE_TREE_VIEWED_MARK = '\u2713';
const STACK_CARD_INDENT_BASE = 4;
const STACK_CARD_INDENT_STEP = 10;
const STACK_GUIDE_OFFSET = 6;
const STACK_GUIDE_STEP = 10;

function buildDiffUnsafeCSS(resolvedTheme: 'dark' | 'light'): string {
  const headerBg = resolvedTheme === 'light' ? '#ececed' : '#16161c';
  const headerBorder = resolvedTheme === 'light' ? 'rgba(0, 0, 0, 0.12)' : 'rgba(255, 255, 255, 0.1)';
  const headerShadow = resolvedTheme === 'light' ? '0 2px 5px rgba(0, 0, 0, 0.06)' : '0 2px 6px rgba(0, 0, 0, 0.35)';
  return `
    [data-diffs-header] {
      position: sticky;
      top: 0;
      z-index: 10;
      min-height: 44px !important;
      background: ${headerBg} !important;
      border-bottom: 1px solid ${headerBorder} !important;
      box-shadow: ${headerShadow};
    }
    [data-selected-line] { background: rgba(250, 204, 21, 0.10) !important; }
    [data-line] span[style*="--diffs-token-light"],
    [data-line] span[style*="--diffs-token-dark"] {
      --shortcake-token-color-light: var(--diffs-token-light, var(--diffs-fg));
      --shortcake-token-color-dark: var(--diffs-token-dark, var(--diffs-fg));
      --shortcake-token-bg-light: var(--diffs-token-light-bg, inherit);
      --shortcake-token-bg-dark: var(--diffs-token-dark-bg, inherit);
      --shortcake-token-font-weight-light: var(--diffs-token-light-font-weight, inherit);
      --shortcake-token-font-weight-dark: var(--diffs-token-dark-font-weight, inherit);
      --shortcake-token-font-style-light: var(--diffs-token-light-font-style, inherit);
      --shortcake-token-font-style-dark: var(--diffs-token-dark-font-style, inherit);
      --shortcake-token-text-decoration-light: var(--diffs-token-light-text-decoration, inherit);
      --shortcake-token-text-decoration-dark: var(--diffs-token-dark-text-decoration, inherit);
      --shortcake-token-color: var(--shortcake-token-color-${resolvedTheme});
      --shortcake-token-bg: var(--shortcake-token-bg-${resolvedTheme});
      --shortcake-token-font-weight: var(--shortcake-token-font-weight-${resolvedTheme});
      --shortcake-token-font-style: var(--shortcake-token-font-style-${resolvedTheme});
      --shortcake-token-text-decoration: var(--shortcake-token-text-decoration-${resolvedTheme});
      color: var(--shortcake-token-color) !important;
      background-color: var(--shortcake-token-bg) !important;
      font-weight: var(--shortcake-token-font-weight);
      font-style: var(--shortcake-token-font-style);
      -webkit-text-decoration: var(--shortcake-token-text-decoration);
      text-decoration: var(--shortcake-token-text-decoration);
    }
  `;
}

function splitPatchIntoFiles(patch: string): string[] {
  if (patch.trim() === '') {
    return [];
  }

  const sections = patch.split(/^diff --git /m);
  if (sections.length <= 1) {
    return [patch];
  }

  return sections
    .slice(1)
    .map((section) => `diff --git ${section}`.trim())
    .filter((section) => section !== '');
}

async function fetchJSON<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  const payload: unknown = await response.json();

  if (!response.ok) {
    const message =
      typeof payload === 'object' &&
      payload !== null &&
      'error' in payload &&
      typeof payload.error === 'string'
        ? payload.error
        : `Request failed (${response.status})`;
    throw new Error(message);
  }

  return payload as T;
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const payload: unknown = await response.json();

  if (!response.ok) {
    const message =
      typeof payload === 'object' &&
      payload !== null &&
      'error' in payload &&
      typeof payload.error === 'string'
        ? payload.error
        : `Request failed (${response.status})`;
    throw new Error(message);
  }

  return payload as T;
}

function parsePatchStatus(patch: string): GitStatusEntry['status'] {
  if (/^new file mode /m.test(patch)) return 'added';
  if (/^deleted file mode /m.test(patch)) return 'deleted';
  if (/^rename from /m.test(patch)) return 'renamed';
  return 'modified';
}

function parseFileInfo(patch: string, index: number): FileInfo {
  const headerMatch = patch.match(/^diff --git a\/.+ b\/(.+)$/m);
  const path = headerMatch?.[1] ?? `file-${index}`;
  const name = path.split('/').pop() ?? path;

  let additions = 0;
  let deletions = 0;
  let inHunk = false;

  for (const line of patch.split('\n')) {
    if (line.startsWith('@@')) {
      inHunk = true;
      continue;
    }
    if (!inHunk) continue;
    if (line.startsWith('+') && !line.startsWith('+++')) additions++;
    else if (line.startsWith('-') && !line.startsWith('---')) deletions++;
  }

  return { path, name, additions, deletions, status: parsePatchStatus(patch), patchIndex: index };
}

function buildChangedFilesTreeUnsafeCSS(): string {
  return `
    [data-type='item'] {
      transition: color 100ms ease-in-out, background-color 100ms ease-in-out;
    }
    [data-type='item']:hover {
      color: var(--trees-selected-fg);
      background: var(--trees-bg-muted);
    }
    [data-item-section='content'] {
      min-width: 0;
    }
    [data-item-section='decoration'] {
      font-family: var(--trees-font-family);
      font-size: 0.6rem;
      font-weight: 500;
      letter-spacing: 0;
      color: var(--trees-fg-muted);
    }
    [data-type='item'][data-item-selected='true'] [data-item-section='decoration'] {
      color: var(--trees-selected-fg);
    }
    [data-file-tree-search-container] {
      margin-bottom: 8px;
    }
    [data-file-tree-search-input] {
      border-color: var(--color-border);
      transition: border-color 150ms ease-in-out;
    }
    [data-file-tree-search-input]::placeholder {
      color: var(--color-text-muted);
    }
    [data-file-tree-search-input]:focus-visible,
    [data-file-tree-search-input][data-file-tree-search-input-fake-focus='true'] {
      border-color: color-mix(in lab, var(--color-accent) 40%, transparent);
      outline: none;
    }
  `;
}

function formatFileTreeDecoration(file: FileInfo, isViewed: boolean): string {
  const parts: string[] = [];
  if (isViewed) parts.push(FILE_TREE_VIEWED_MARK);
  if (file.additions > 0) parts.push(`+${file.additions}`);
  if (file.deletions > 0) parts.push(`-${file.deletions}`);
  return parts.join(' ');
}

function getFileTreeDecorationTitle(file: FileInfo, isViewed: boolean): string {
  const parts: string[] = [];
  if (isViewed) parts.push('Viewed');
  if (file.additions > 0) {
    parts.push(`${file.additions} addition${file.additions === 1 ? '' : 's'}`);
  }
  if (file.deletions > 0) {
    parts.push(`${file.deletions} deletion${file.deletions === 1 ? '' : 's'}`);
  }
  return parts.join(', ');
}

type ChangedFilesTreeProps = {
  fileInfos: FileInfo[];
  fileFilter: string;
  activeFileIndex: number | null;
  viewedFiles: Set<string>;
  resolvedTheme: 'dark' | 'light';
  onFilterChange: (value: string) => void;
  onFileClick: (index: number) => void;
};

function ChangedFilesTree({
  fileInfos,
  fileFilter,
  activeFileIndex,
  viewedFiles,
  resolvedTheme,
  onFilterChange,
  onFileClick,
}: ChangedFilesTreeProps) {
  const paths = useMemo(() => fileInfos.map((file) => file.path), [fileInfos]);
  const fileByPath = useMemo(
    () => new Map(fileInfos.map((file) => [file.path, file])),
    [fileInfos],
  );
  const gitStatus = useMemo<GitStatusEntry[]>(
    () => fileInfos.map((file) => ({ path: file.path, status: file.status })),
    [fileInfos],
  );
  const activePath = activeFileIndex == null ? null : fileInfos[activeFileIndex]?.path ?? null;
  const fileByPathRef = useRef(fileByPath);
  const viewedFilesRef = useRef(viewedFiles);

  useEffect(() => {
    fileByPathRef.current = fileByPath;
  }, [fileByPath]);

  useEffect(() => {
    viewedFilesRef.current = viewedFiles;
  }, [viewedFiles]);

  const renderRowDecoration = useCallback(({ item }: { item: { kind: string; path: string } }): FileTreeRowDecoration | null => {
    if (item.kind !== 'file') return null;
    const file = fileByPathRef.current.get(item.path);
    if (!file) return null;

    const isViewed = viewedFilesRef.current.has(item.path);
    const text = formatFileTreeDecoration(file, isViewed);
    if (!text) return null;

    return {
      text,
      title: getFileTreeDecorationTitle(file, isViewed),
    };
  }, []);

  const handleSelectionChange = useCallback((selectedPaths: readonly string[]) => {
    const selectedPath = [...selectedPaths]
      .reverse()
      .find((path) => fileByPathRef.current.has(path));
    if (!selectedPath) return;

    const file = fileByPathRef.current.get(selectedPath);
    if (file) onFileClick(file.patchIndex);
  }, [onFileClick]);

  const { model } = useFileTree({
    density: 'compact',
    fileTreeSearchMode: 'hide-non-matches',
    flattenEmptyDirectories: false,
    gitStatus,
    initialExpansion: 'open',
    initialSearchQuery: fileFilter || null,
    initialSelectedPaths: activePath ? [activePath] : [],
    itemHeight: 26,
    onSearchChange: (value) => onFilterChange(value ?? ''),
    onSelectionChange: handleSelectionChange,
    overscan: 16,
    paths,
    renderRowDecoration,
    search: true,
    searchBlurBehavior: 'retain',
    stickyFolders: false,
    unsafeCSS: buildChangedFilesTreeUnsafeCSS(),
  });

  useEffect(() => {
    model.resetPaths(paths);
  }, [model, paths]);

  useEffect(() => {
    model.setSearch(fileFilter.trim() ? fileFilter : null);
  }, [fileFilter, model]);

  useEffect(() => {
    model.setGitStatus(gitStatus);
  }, [gitStatus, model, viewedFiles]);

  useEffect(() => {
    const selectedPaths = model.getSelectedPaths();

    if (activePath) {
      if (!selectedPaths.includes(activePath)) {
        model.getItem(activePath)?.select();
      }
      model.scrollToPath(activePath, { focus: false, offset: 'nearest' });
      return;
    }

    for (const path of selectedPaths) {
      model.getItem(path)?.deselect();
    }
  }, [activePath, model]);

  const treeStyle = useMemo(
    () => ({
      height: '100%',
      width: '100%',
      '--trees-accent-override': 'var(--color-accent)',
      '--trees-bg-muted-override': 'var(--color-surface-hover)',
      '--trees-bg-override': 'transparent',
      '--trees-border-color-override': 'transparent',
      '--trees-file-icon-color-default': 'var(--color-text-muted)',
      '--trees-fg-muted-override': 'var(--color-text-muted)',
      '--trees-fg-override': 'var(--color-text-secondary)',
      '--trees-focus-ring-color-override': 'color-mix(in lab, var(--color-accent) 42%, transparent)',
      '--trees-font-family-override': 'var(--font-mono)',
      '--trees-font-size-override': '0.72rem',
      '--trees-icon-width-override': '14px',
      '--trees-item-margin-x-override': '2px',
      '--trees-item-padding-x-override': '10px',
      '--trees-level-gap-override': '14px',
      '--trees-padding-inline-override': '0px',
      '--trees-search-bg-override': 'var(--color-surface-hover)',
      '--trees-search-fg-override': 'var(--color-text-primary)',
      '--trees-search-font-weight-override': '400',
      '--trees-selected-bg-override': 'var(--color-accent-bg)',
      '--trees-selected-fg-override': 'var(--color-text-primary)',
      '--trees-status-added-override': 'var(--color-stat-add)',
      '--trees-status-deleted-override': 'var(--color-stat-del)',
      '--trees-status-modified-override': 'var(--color-accent)',
      '--trees-status-renamed-override': resolvedTheme === 'light' ? '#a16207' : '#facc15',
    }) as React.CSSProperties,
    [resolvedTheme],
  );

  return (
    <div className="flex-1 min-h-0 overflow-hidden px-2.5 pt-2 pb-1.5">
      <PierreFileTree
        className="block h-full w-full"
        model={model}
        style={treeStyle}
      />
    </div>
  );
}

function CommentInput({
  onSubmit,
  onCancel,
  initialText = '',
  lineLabel,
}: {
  onSubmit: (text: string) => void;
  onCancel: () => void;
  initialText?: string;
  lineLabel?: string;
}) {
  const [text, setText] = useState(initialText);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      if (text.trim()) onSubmit(text.trim());
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onCancel();
    }
  };

  return (
    <div
      className="flex flex-col gap-1.5 p-2.5 my-1 bg-surface-hover border border-border rounded-md"
      onClick={(e) => e.stopPropagation()}
    >
      {lineLabel && (
        <span className="font-mono text-[0.65rem] text-text-muted">{lineLabel}</span>
      )}
      <textarea
        ref={textareaRef}
        className="w-full min-h-[60px] bg-surface border border-border rounded-md text-text-primary font-mono text-[0.75rem] p-2 resize-y outline-none focus:border-border-strong placeholder:text-text-muted"
        placeholder="Add a comment..."
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
      />
      <div className="flex items-center gap-1.5 justify-end">
        <span className="text-[0.65rem] text-text-muted mr-auto">
          {navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'}+Enter to submit
        </span>
        <button
          type="button"
          className="appearance-none border border-border bg-transparent text-text-secondary text-[0.72rem] font-mono px-2.5 py-1 rounded-md cursor-pointer hover:bg-surface-hover hover:text-text-primary transition-colors duration-100"
          onClick={onCancel}
        >
          Cancel
        </button>
        <button
          type="button"
          className="appearance-none border border-accent bg-accent/10 text-accent text-[0.72rem] font-mono px-2.5 py-1 rounded-md cursor-pointer hover:bg-accent/20 transition-colors duration-100 disabled:opacity-40 disabled:cursor-not-allowed"
          disabled={!text.trim()}
          onClick={() => { if (text.trim()) onSubmit(text.trim()); }}
        >
          Add
        </button>
      </div>
    </div>
  );
}

function SavedComment({
  comment,
  onEdit,
  onDelete,
}: {
  comment: DiffComment;
  onEdit: () => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="flex items-start gap-2 p-2.5 my-1 bg-surface-hover border border-border rounded-md group">
      <div className="flex-1 min-w-0">
        <span className="font-mono text-[0.65rem] text-text-muted">
          {formatLineLabel(comment.startLine, comment.endLine)}
        </span>
        <p className="text-text-primary font-mono text-[0.75rem] m-0 mt-0.5 whitespace-pre-wrap break-words">
          {comment.text}
        </p>
      </div>
      <div className="flex gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity duration-100">
        <button
          type="button"
          className="appearance-none border-none bg-transparent text-text-muted text-[0.65rem] cursor-pointer hover:text-text-primary p-0.5"
          onClick={(e) => { e.stopPropagation(); onEdit(); }}
          title="Edit"
        >
          ✎
        </button>
        <button
          type="button"
          className="appearance-none border-none bg-transparent text-text-muted text-[0.65rem] cursor-pointer hover:text-danger p-0.5"
          onClick={(e) => { e.stopPropagation(); onDelete(comment.id); }}
          title="Delete"
        >
          ✕
        </button>
      </div>
    </div>
  );
}

const MODEL_COLORS: Record<string, string> = {
  claude: 'border-l-orange-400',
  codex: 'border-l-green-400',
  synthesis: 'border-l-purple-400',
};

const SEVERITY_COLORS: Record<string, string> = {
  error: 'text-red-400',
  warning: 'text-yellow-400',
  suggestion: 'text-blue-400',
  info: 'text-text-muted',
};

function formatModelLabel(model: string): string {
  // "synthesis:claude:opus" -> "Synthesis"
  // "claude:sonnet" -> "Claude Sonnet"
  // "codex:gpt-5.4" -> "Codex GPT-5.4"
  if (model.startsWith('synthesis:')) return 'Synthesis';
  const parts = model.split(':');
  if (parts.length === 2) {
    return `${parts[0]!.charAt(0).toUpperCase() + parts[0]!.slice(1)} ${parts[1]!.toUpperCase().startsWith('GPT') ? parts[1] : parts[1]!.charAt(0).toUpperCase() + parts[1]!.slice(1)}`;
  }
  return model;
}

function AIComment({
  comment,
  onDelete,
}: {
  comment: DiffComment;
  onDelete: (id: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const [copiedFix, setCopiedFix] = useState(false);
  const source = comment.source!;
  const tool = source.model.split(':')[0] ?? source.model;
  const borderClass = MODEL_COLORS[tool] ?? 'border-l-accent';
  const severityClass = SEVERITY_COLORS[source.severity] ?? 'text-text-muted';
  const modelLabel = formatModelLabel(source.model);
  const handleCopy = useCallback(() => {
    const ref = formatLineRef(comment.file, comment.startLine, comment.endLine);
    const copyText = `${ref}\n${comment.text}`;
    void navigator.clipboard.writeText(copyText).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [comment.file, comment.startLine, comment.endLine, comment.text]);
  const handleCopyFix = useCallback(() => {
    const lineRange = comment.startLine === comment.endLine
      ? `line ${comment.startLine}`
      : `lines ${comment.startLine}-${comment.endLine}`;
    const fixPrompt = `In ${comment.file} at ${lineRange}: ${comment.text}`;
    void navigator.clipboard.writeText(fixPrompt).then(() => {
      setCopiedFix(true);
      setTimeout(() => setCopiedFix(false), 1500);
    });
  }, [comment.file, comment.startLine, comment.endLine, comment.text]);
  return (
    <div className={`flex flex-col gap-1.5 p-3 my-1.5 bg-yellow-500/[0.06] border border-yellow-500/20 ${borderClass} border-l-2 max-w-[720px] group`}>
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-[0.62rem] text-text-primary bg-surface-active px-1.5 py-0.5 rounded-sm font-medium">
          {modelLabel}
        </span>
        <span className={`font-mono text-[0.58rem] uppercase tracking-wider font-semibold ${severityClass}`}>
          {source.severity}
        </span>
        <span className="font-mono text-[0.58rem] text-text-muted ml-auto">
          {formatLineLabel(comment.startLine, comment.endLine)}
        </span>
      </div>
      <p className="text-text-primary font-mono text-[0.78rem] m-0 whitespace-pre-wrap break-words leading-relaxed select-text">
        {comment.text}
      </p>
      <div className="flex items-center gap-2 pt-0.5 opacity-0 group-hover:opacity-100 transition-opacity duration-100">
        <button
          type="button"
          className="appearance-none border border-accent/30 bg-accent/5 text-accent text-[0.6rem] font-mono px-2 py-[2px] rounded cursor-pointer hover:bg-accent/15 transition-colors duration-100"
          onClick={(e) => { e.stopPropagation(); handleCopyFix(); }}
          title="Copy fix prompt to clipboard"
        >
          {copiedFix ? 'Copied!' : 'Copy fix prompt'}
        </button>
        <button
          type="button"
          className="appearance-none border border-border bg-transparent text-text-muted text-[0.6rem] font-mono px-2 py-[2px] rounded cursor-pointer hover:bg-surface-hover hover:text-text-primary transition-colors duration-100"
          onClick={(e) => { e.stopPropagation(); handleCopy(); }}
          title="Copy comment"
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
        <button
          type="button"
          className="appearance-none border-none bg-transparent text-text-muted text-[0.6rem] font-mono px-1 py-[2px] cursor-pointer hover:text-danger transition-colors duration-100 ml-auto"
          onClick={(e) => { e.stopPropagation(); onDelete(comment.id); }}
          title="Dismiss"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

function ReviewDialog({
  models,
  onStart,
  onClose,
  isReviewing,
}: {
  models: ReviewModel[];
  onStart: (selectedModels: string[], synthesizeWith: string | null) => void;
  onClose: () => void;
  isReviewing: boolean;
}) {
  const [selected, setSelected] = useState<Set<string>>(() => {
    // Default: first variant of each available tool
    const seen = new Set<string>();
    const defaults = new Set<string>();
    for (const m of models) {
      if (m.available && !seen.has(m.tool)) {
        seen.add(m.tool);
        defaults.add(m.id);
      }
    }
    return defaults;
  });
  const [synthesize, setSynthesize] = useState(false);
  const [synthesisModel, setSynthesisModel] = useState<string>(() => {
    const first = models.find((m) => m.available);
    return first?.id ?? '';
  });

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Group models by tool
  const groups = useMemo(() => {
    const map = new Map<string, ReviewModel[]>();
    for (const m of models) {
      let arr = map.get(m.tool);
      if (!arr) { arr = []; map.set(m.tool, arr); }
      arr.push(m);
    }
    return map;
  }, [models]);

  const availableModels = useMemo(
    () => models.filter((m) => m.available),
    [models],
  );

  return (
    <div className="bg-surface border border-border rounded-lg shadow-lg p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[0.8rem] text-text-primary font-medium">AI Review</span>
        <button
          type="button"
          className="appearance-none border-none bg-transparent text-text-muted text-[0.65rem] cursor-pointer hover:text-text-primary p-0.5"
          onClick={onClose}
        >
          ✕
        </button>
      </div>
      <div className="flex flex-col gap-3">
        {[...groups.entries()].map(([tool, toolModels]) => (
          <div key={tool}>
            <div className="flex items-center gap-1.5 mb-1.5">
              <span className="font-mono text-[0.68rem] text-text-secondary font-semibold capitalize">{tool}</span>
              {!toolModels[0]?.available && (
                <span className="font-mono text-[0.58rem] text-text-muted">(not installed)</span>
              )}
            </div>
            <div className="flex flex-col gap-1 pl-1">
              {toolModels.map((m) => (
                <label key={m.id} className={`flex items-center gap-2 font-mono text-[0.75rem] ${m.available ? 'text-text-primary cursor-pointer' : 'text-text-muted cursor-not-allowed'}`}>
                  <input
                    type="checkbox"
                    checked={selected.has(m.id)}
                    onChange={() => toggle(m.id)}
                    disabled={!m.available || isReviewing}
                    className="accent-accent"
                  />
                  {m.name.replace(`${tool.charAt(0).toUpperCase() + tool.slice(1)} `, '')}
                </label>
              ))}
            </div>
          </div>
        ))}
      </div>
      {selected.size >= 2 && (
        <div className="border-t border-border pt-3 flex flex-col gap-2">
          <label className="flex items-center gap-2 font-mono text-[0.75rem] text-text-primary cursor-pointer">
            <input
              type="checkbox"
              checked={synthesize}
              onChange={() => setSynthesize((p) => !p)}
              disabled={isReviewing}
              className="accent-accent"
            />
            Final synthesis pass
          </label>
          {synthesize && (
            <select
              className="appearance-none bg-surface-hover border border-border rounded-md text-text-primary font-mono text-[0.72rem] px-2 py-1 outline-none cursor-pointer"
              value={synthesisModel}
              onChange={(e) => setSynthesisModel(e.target.value)}
              disabled={isReviewing}
            >
              {availableModels.map((m) => (
                <option key={m.id} value={m.id}>{m.name}</option>
              ))}
            </select>
          )}
          {synthesize && (
            <p className="font-mono text-[0.6rem] text-text-muted m-0">
              After all reviews complete, one model reads all findings and produces a consolidated review.
            </p>
          )}
        </div>
      )}
      <button
        type="button"
        className="appearance-none border border-accent bg-accent/10 text-accent text-[0.72rem] font-mono px-3 py-1.5 rounded-md cursor-pointer hover:bg-accent/20 transition-colors duration-100 disabled:opacity-40 disabled:cursor-not-allowed self-end"
        disabled={selected.size === 0 || isReviewing}
        onClick={() => onStart([...selected], synthesize ? synthesisModel : null)}
      >
        {isReviewing ? 'Reviewing...' : `Review with ${selected.size} model${selected.size === 1 ? '' : 's'}${synthesize ? ' + synthesis' : ''}`}
      </button>
    </div>
  );
}

function ReviewSummaryPanel({
  summaries,
  fixPrompt,
  onClose,
}: {
  summaries: Map<string, string>;
  fixPrompt: string | null;
  onClose: () => void;
}) {
  const [copiedFix, setCopiedFix] = useState(false);
  const handleCopyFix = useCallback(() => {
    if (!fixPrompt) return;
    void navigator.clipboard.writeText(fixPrompt).then(() => {
      setCopiedFix(true);
      setTimeout(() => setCopiedFix(false), 2000);
    });
  }, [fixPrompt]);

  if (summaries.size === 0) return null;

  const individual = [...summaries.entries()].filter(([k]) => !k.startsWith('synthesis'));
  const synthesis = [...summaries.entries()].filter(([k]) => k.startsWith('synthesis'));

  return (
    <div className="mx-4 mt-3 mb-1 bg-surface-hover border border-border rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <span className="font-mono text-[0.7rem] text-text-primary font-medium">Review Summary</span>
        <button
          type="button"
          className="appearance-none border-none bg-transparent text-text-muted text-[0.6rem] cursor-pointer hover:text-text-primary"
          onClick={onClose}
        >
          ✕
        </button>
      </div>
      {individual.length > 0 && synthesis.length > 0 && (
        <div className="px-3 py-1.5 border-b border-border">
          <span className="font-mono text-[0.6rem] text-text-muted uppercase tracking-wider">Individual reviews</span>
        </div>
      )}
      {individual.map(([model, summary]) => (
        <div key={model} className="px-3 py-2 border-b border-border last:border-b-0">
          <span className="font-mono text-[0.6rem] text-text-muted bg-surface-active px-1.5 py-0.5 rounded mr-2">{model}</span>
          <p className="font-mono text-[0.72rem] text-text-secondary m-0 mt-1">{summary}</p>
        </div>
      ))}
      {synthesis.length > 0 && (
        <div className="border-t-2 border-purple-500/20 bg-purple-500/[0.03]">
          <div className="px-3 py-1.5 border-b border-purple-500/10">
            <span className="font-mono text-[0.6rem] text-purple-400 uppercase tracking-wider font-semibold">Synthesis</span>
          </div>
          {synthesis.map(([model, summary]) => (
            <div key={model} className="px-3 py-2 border-b border-purple-500/10 last:border-b-0">
              <span className="font-mono text-[0.6rem] text-purple-300 bg-purple-500/10 px-1.5 py-0.5 rounded mr-2">{model}</span>
              <p className="font-mono text-[0.72rem] text-text-primary m-0 mt-1">{summary}</p>
            </div>
          ))}
        </div>
      )}
      {fixPrompt && (
        <div className="px-3 py-2.5 border-t border-purple-500/20 bg-purple-500/[0.04]">
          <div className="flex items-center justify-between mb-1.5">
            <span className="font-mono text-[0.65rem] text-purple-400 font-semibold">Fix prompt</span>
            <button
              type="button"
              className="appearance-none border border-purple-500/30 bg-purple-500/10 text-purple-400 text-[0.65rem] font-mono px-2 py-0.5 rounded cursor-pointer hover:bg-purple-500/20 transition-colors duration-100"
              onClick={handleCopyFix}
            >
              {copiedFix ? 'Copied!' : 'Copy prompt'}
            </button>
          </div>
          <p className="font-mono text-[0.72rem] text-text-primary m-0 whitespace-pre-wrap break-words leading-relaxed select-text">{fixPrompt}</p>
        </div>
      )}
    </div>
  );
}

function SelectionToolbar({
  lineLabel,
  onComment,
  onSplit,
}: {
  lineLabel: string;
  onComment: () => void;
  onSplit?: () => void;
}) {
  return (
    <div
      className="flex items-center gap-2 p-2 my-1 bg-surface-hover border border-border rounded-md"
      onClick={(e) => e.stopPropagation()}
    >
      <span className="font-mono text-[0.65rem] text-text-muted mr-auto">{lineLabel}</span>
      {onSplit && (
        <button
          type="button"
          className="appearance-none border border-green-500/40 bg-green-500/10 text-green-400 text-[0.72rem] font-mono px-2.5 py-1 rounded-md cursor-pointer hover:bg-green-500/20 transition-colors duration-100 flex items-center gap-1"
          onClick={onSplit}
        >
          Split
        </button>
      )}
      <button
        type="button"
        className="appearance-none border border-border bg-transparent text-text-secondary text-[0.72rem] font-mono px-2.5 py-1 rounded-md cursor-pointer hover:bg-surface-hover hover:text-text-primary transition-colors duration-100 flex items-center gap-1"
        onClick={onComment}
      >
        Comment
      </button>
    </div>
  );
}

function SavedSplitSelection({
  selection: sel,
  onDelete,
}: {
  selection: SplitLineSelection;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="flex items-center gap-2 p-2 my-1 bg-green-500/[0.06] border border-green-500/20 rounded-md group">
      <span className="font-mono text-[0.65rem] text-green-400">
        {formatLineLabel(sel.startLine, sel.endLine)}
      </span>
      <span className="font-mono text-[0.6rem] text-text-muted">
        selected for split
      </span>
      <button
        type="button"
        className="appearance-none border-none bg-transparent text-text-muted text-[0.65rem] cursor-pointer hover:text-danger p-0.5 ml-auto opacity-0 group-hover:opacity-100 transition-opacity duration-100"
        onClick={(e) => { e.stopPropagation(); onDelete(sel.id); }}
        title="Remove"
      >
        ✕
      </button>
    </div>
  );
}

const DIFF_HEADER_HEIGHT = 'min-h-[44px]';

function ViewedToggle({
  isViewed,
  onToggle,
}: {
  isViewed: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className={`flex items-center gap-1.5 appearance-none border-0 bg-transparent px-1 py-0.5 rounded cursor-pointer select-none font-mono text-[0.7rem] transition-colors duration-100 ${
        isViewed ? 'text-accent' : 'text-text-secondary hover:text-text-primary'
      }`}
      onClick={(e) => { e.stopPropagation(); onToggle(); }}
    >
      <span
        className={`inline-flex items-center justify-center w-[15px] h-[15px] rounded-[3px] border text-[0.6rem] leading-none shrink-0 transition-colors duration-100 ${
          isViewed
            ? 'bg-accent border-accent text-white'
            : 'bg-surface border-border-strong hover:border-text-muted'
        }`}
      >
        {isViewed ? '\u2713' : ''}
      </span>
      Viewed
    </button>
  );
}

function ViewedFileHeader({
  fileInfo,
  onToggle,
}: {
  fileInfo: FileInfo;
  isViewed: boolean;
  onToggle: (path: string) => void;
}) {
  return (
    <div
      className={`flex items-center gap-3 px-4 ${DIFF_HEADER_HEIGHT} border-b border-border-strong bg-surface-hover hover:bg-surface-active cursor-pointer select-none transition-colors duration-100`}
      onClick={() => onToggle(fileInfo.path)}
    >
      <span className="font-mono text-[0.72rem] truncate text-text-muted">
        {fileInfo.path}
      </span>
      <span className="ml-auto flex items-center gap-3 shrink-0">
        <span className="flex gap-[5px] text-[0.6rem] opacity-70">
          {fileInfo.additions > 0 && (
            <span className="text-stat-add">+{fileInfo.additions}</span>
          )}
          {fileInfo.deletions > 0 && (
            <span className="text-stat-del">-{fileInfo.deletions}</span>
          )}
        </span>
        <ViewedToggle isViewed onToggle={() => onToggle(fileInfo.path)} />
      </span>
    </div>
  );
}

function LazyDiffFileSection({
  index,
  fileInfo,
  renderContent,
}: {
  index: number;
  fileInfo: FileInfo;
  renderContent: () => React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(index === 0);

  useEffect(() => {
    if (visible || !ref.current) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: '200px' },
    );
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [visible]);

  if (visible) return <>{renderContent()}</>;

  const skeletonLines = Math.min(Math.max(fileInfo.additions + fileInfo.deletions, 3), 8);

  return (
    <div ref={ref}>
      <div className="flex items-center gap-2 px-4 min-h-[44px] bg-surface-hover border-b border-border-strong">
        <span className="font-mono text-[0.72rem] text-text-secondary truncate">
          {fileInfo.path}
        </span>
        <span className="ml-auto flex gap-[5px] text-[0.6rem] shrink-0">
          {fileInfo.additions > 0 && (
            <span className="text-stat-add">+{fileInfo.additions}</span>
          )}
          {fileInfo.deletions > 0 && (
            <span className="text-stat-del">-{fileInfo.deletions}</span>
          )}
        </span>
      </div>
      <div className="px-3 py-2 flex flex-col gap-[6px]">
        {Array.from({ length: skeletonLines }, (_, i) => (
          <div
            key={i}
            className="h-[14px] rounded-sm animate-pulse"
            style={{
              width: `${30 + ((i * 37) % 50)}%`,
              backgroundColor: 'var(--color-surface-hover)',
              opacity: 0.5 + (i % 3) * 0.15,
            }}
          />
        ))}
      </div>
    </div>
  );
}

const LARGE_FILE_THRESHOLD = 500;

function LargeFilePlaceholder({
  fileInfo,
  onShow,
  onToggleViewed,
}: {
  fileInfo: FileInfo;
  onShow: () => void;
  onToggleViewed?: (path: string) => void;
}) {
  return (
    <div>
      <div className="flex items-center gap-2 px-4 min-h-[44px] bg-surface-hover border-b border-border-strong">
        <span className="font-mono text-[0.72rem] text-text-secondary truncate">
          {fileInfo.path}
        </span>
        <span className="ml-auto flex gap-[5px] text-[0.6rem] shrink-0">
          {fileInfo.additions > 0 && (
            <span className="text-stat-add">+{fileInfo.additions}</span>
          )}
          {fileInfo.deletions > 0 && (
            <span className="text-stat-del">-{fileInfo.deletions}</span>
          )}
        </span>
        {onToggleViewed && (
          <button
            type="button"
            className="appearance-none border border-border bg-transparent text-text-muted text-[0.65rem] font-mono px-2 py-0.5 rounded cursor-pointer hover:bg-surface-hover hover:text-text-primary hover:border-border-strong transition-colors duration-100 whitespace-nowrap"
            onClick={(e) => { e.stopPropagation(); onToggleViewed(fileInfo.path); }}
          >
            Viewed
          </button>
        )}
      </div>
      <div className="flex flex-col items-center justify-center py-8 gap-2 mx-3 my-3 rounded-md border border-yellow-500/30 bg-yellow-500/[0.06]">
        <p className="text-[0.8rem] text-yellow-200/80">
          Large file — <span className="font-mono text-[0.72rem]">{fileInfo.additions + fileInfo.deletions}</span> lines changed
        </p>
        <button
          type="button"
          className="appearance-none border border-yellow-500/40 bg-yellow-500/10 text-yellow-200/90 text-[0.75rem] font-mono px-3 py-1 rounded cursor-pointer hover:bg-yellow-500/20 hover:text-yellow-100 hover:border-yellow-500/60 transition-colors duration-100"
          onClick={onShow}
        >
          Show changes
        </button>
      </div>
    </div>
  );
}

function SplitLinesDialog({
  selectionCount,
  onSubmit,
  onCancel,
  isSplitting,
  splitError,
}: {
  selectionCount: number;
  onSubmit: (commitMessage: string) => void;
  onCancel: () => void;
  isSplitting: boolean;
  splitError: string | null;
}) {
  const [commitMessage, setCommitMessage] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && commitMessage.trim()) {
      e.preventDefault();
      onSubmit(commitMessage.trim());
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onCancel();
    }
  };

  return (
    <div
      className="flex flex-col gap-2 p-3 bg-surface border border-border rounded-lg shadow-lg"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between mb-0.5">
        <span className="font-mono text-[0.72rem] font-semibold text-text-primary">
          Split {selectionCount} line selection{selectionCount === 1 ? '' : 's'} into new branch
        </span>
        <button
          type="button"
          className="appearance-none border-none bg-transparent text-text-muted text-[0.65rem] cursor-pointer hover:text-text-primary p-0.5"
          onClick={onCancel}
          disabled={isSplitting}
        >
          Cancel
        </button>
      </div>
      {isSplitting && (
        <p className="text-[0.72rem] text-text-muted font-mono m-0">Splitting...</p>
      )}
      {splitError && (
        <p className="text-[0.72rem] text-danger font-mono m-0">{splitError}</p>
      )}
      {!isSplitting && (
        <>
          <input
            ref={inputRef}
            type="text"
            className="w-full bg-surface border border-border rounded-md text-text-primary font-mono text-[0.75rem] px-2.5 py-1.5 outline-none focus:border-border-strong placeholder:text-text-muted"
            placeholder="Commit message for new branch..."
            value={commitMessage}
            onChange={(e) => setCommitMessage(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <p className="text-[0.6rem] text-text-muted font-mono m-0">
            Selected lines will be split into a new branch placed before the current one.
          </p>
          <button
            type="button"
            className="appearance-none border border-green-500/40 bg-green-500/10 text-green-400 text-[0.72rem] font-mono px-3 py-1.5 rounded-md cursor-pointer hover:bg-green-500/20 transition-colors duration-100 disabled:opacity-40 disabled:cursor-not-allowed self-end"
            disabled={!commitMessage.trim()}
            onClick={() => { if (commitMessage.trim()) onSubmit(commitMessage.trim()); }}
          >
            Split
          </button>
        </>
      )}
    </div>
  );
}

const EMPTY_COMMENTS: DiffComment[] = [];
const EMPTY_SPLIT_SELECTIONS: SplitLineSelection[] = [];

const DiffFileSection = React.memo(function DiffFileSection({
  patch,
  fileInfo,
  fileComments,
  activeInput,
  editingComment,
  toolbarState,
  onRangeSelected,
  onStartEdit,
  onAddComment,
  onUpdateComment,
  onDeleteComment,
  onCancelInput,
  onToolbarComment,
  onToolbarSplit,
  fileSplitSelections,
  onDeleteSplitSelection,
  diffStyle,
  resolvedTheme,
  diffTheme,
  onToggleViewed,
}: {
  patch: string;
  fileInfo: FileInfo;
  fileComments: DiffComment[];
  activeInput: ActiveInput;
  editingComment: DiffComment | null;
  toolbarState: ActiveInput;
  onRangeSelected: (file: string, startLine: number, endLine: number, side: AnnotationSide) => void;
  onStartEdit: (comment: DiffComment) => void;
  onAddComment: (file: string, startLine: number, endLine: number, side: AnnotationSide, text: string) => void;
  onUpdateComment: (id: string, text: string) => void;
  onDeleteComment: (id: string) => void;
  onCancelInput: () => void;
  onToolbarComment: () => void;
  onToolbarSplit?: () => void;
  fileSplitSelections: SplitLineSelection[];
  onDeleteSplitSelection?: (id: string) => void;
  diffStyle: DiffStyle;
  resolvedTheme?: 'dark' | 'light';
  diffTheme?: string;
  onToggleViewed?: (path: string) => void;
}) {

  const handleSelectionEnd = useCallback(
    (range: SelectedLineRange | null) => {
      if (!range) return;
      const start = Math.min(range.start, range.end);
      const end = Math.max(range.start, range.end);
      const side: AnnotationSide = range.side ?? 'additions';
      onRangeSelected(fileInfo.path, start, end, side);
    },
    [fileInfo.path, onRangeSelected],
  );

  const rt = resolvedTheme ?? 'dark';
  const activeTheme = diffTheme ?? (rt === 'light' ? 'pierre-light' : 'pierre-dark');

  const fileDiff = useMemo(
    () => setLanguageOverride(getSingularPatch(patch), getFiletypeFromFileName(fileInfo.path)),
    [patch, fileInfo.path],
  );
  const shouldPreloadHighlightedDiff = fileDiff.type === 'new' || fileDiff.type === 'deleted';

  const options = useMemo<FileDiffProps<CommentMeta>['options']>(
    () => ({
      diffStyle,
      diffIndicators: 'classic',
      hunkSeparators: 'metadata',
      theme: activeTheme,
      themeType: rt,
      overflow: 'scroll',
      lineDiffType: 'word',
      enableLineSelection: true,
      onLineSelectionEnd: handleSelectionEnd,
      unsafeCSS: buildDiffUnsafeCSS(rt),
    }),
    [diffStyle, handleSelectionEnd, rt, activeTheme],
  );
  const preloadKey = shouldPreloadHighlightedDiff
    ? JSON.stringify([fileInfo.path, patch, diffStyle, activeTheme, rt])
    : '';
  const [preloadedDiffHTML, setPreloadedDiffHTML] = useState<{ key: string; html: string } | null>(null);

  useEffect(() => {
    if (!shouldPreloadHighlightedDiff) {
      setPreloadedDiffHTML(null);
      return;
    }

    let cancelled = false;
    setPreloadedDiffHTML((current) => (current?.key === preloadKey ? current : null));

    preloadDiffHTML({ fileDiff, options })
      .then((html) => {
        if (!cancelled) setPreloadedDiffHTML({ key: preloadKey, html });
      })
      .catch((error) => {
        console.error('Failed to preload diff syntax highlighting', error);
      });

    return () => {
      cancelled = true;
    };
  }, [fileDiff, options, preloadKey, shouldPreloadHighlightedDiff]);
  const prerenderedDiffHTML = shouldPreloadHighlightedDiff && preloadedDiffHTML?.key === preloadKey
    ? preloadedDiffHTML.html
    : undefined;

  const selectedLines = useMemo<SelectedLineRange | null>(() => {
    if (toolbarState && toolbarState.file === fileInfo.path) {
      return { start: toolbarState.startLine, end: toolbarState.endLine, side: toolbarState.side };
    }
    if (activeInput && activeInput.file === fileInfo.path) {
      return { start: activeInput.startLine, end: activeInput.endLine, side: activeInput.side };
    }
    if (editingComment && editingComment.file === fileInfo.path) {
      return { start: editingComment.startLine, end: editingComment.endLine, side: editingComment.side };
    }
    return null;
  }, [toolbarState, activeInput, editingComment, fileInfo.path]);

  const lineAnnotations = useMemo<DiffLineAnnotation<CommentMeta>[]>(() => {
    const annotations: DiffLineAnnotation<CommentMeta>[] = [];

    for (const comment of fileComments) {
      annotations.push({
        lineNumber: comment.endLine,
        side: comment.side,
        metadata: { commentId: comment.id, text: comment.text, isInput: false },
      });
    }

    if (activeInput && activeInput.file === fileInfo.path && !editingComment) {
      annotations.push({
        lineNumber: activeInput.endLine,
        side: activeInput.side,
        metadata: { commentId: '__input__', text: '', isInput: true },
      });
    }

    if (toolbarState && toolbarState.file === fileInfo.path) {
      annotations.push({
        lineNumber: toolbarState.endLine,
        side: toolbarState.side,
        metadata: { commentId: '__toolbar__', text: '', isInput: false, isToolbar: true },
      });
    }

    // Add split line selection annotations
    for (const sel of fileSplitSelections) {
      annotations.push({
        lineNumber: sel.endLine,
        side: sel.side,
        metadata: {
          commentId: `__splitsel__${sel.id}`,
          text: '',
          isInput: false,
          isSplitSelection: true,
          splitSelectionId: sel.id,
        },
      });
    }

    return annotations;
  }, [fileComments, activeInput, editingComment, toolbarState, fileSplitSelections, fileInfo.path]);

  const renderAnnotation = useCallback(
    (annotation: DiffLineAnnotation<CommentMeta>) => {
      const { metadata } = annotation;

      if (metadata.isSplitSelection && metadata.splitSelectionId && onDeleteSplitSelection) {
        const sel = fileSplitSelections.find((s) => s.id === metadata.splitSelectionId);
        if (sel) {
          return (
            <SavedSplitSelection
              selection={sel}
              onDelete={onDeleteSplitSelection}
            />
          );
        }
        return null;
      }

      if (metadata.isToolbar && toolbarState) {
        return (
          <SelectionToolbar
            lineLabel={formatLineLabel(toolbarState.startLine, toolbarState.endLine)}
            onComment={onToolbarComment}
            onSplit={onToolbarSplit}
          />
        );
      }

      if (metadata.isInput && activeInput) {
        return (
          <CommentInput
            lineLabel={formatLineLabel(activeInput.startLine, activeInput.endLine)}
            onSubmit={(text) =>
              onAddComment(fileInfo.path, activeInput.startLine, activeInput.endLine, activeInput.side, text)
            }
            onCancel={onCancelInput}
          />
        );
      }

      const comment = fileComments.find((c) => c.id === metadata.commentId);
      if (!comment) return null;

      if (editingComment && editingComment.id === comment.id) {
        return (
          <CommentInput
            initialText={comment.text}
            lineLabel={formatLineLabel(comment.startLine, comment.endLine)}
            onSubmit={(text) => onUpdateComment(comment.id, text)}
            onCancel={onCancelInput}
          />
        );
      }

      if (comment.source?.type === 'ai') {
        return (
          <AIComment
            comment={comment}
            onDelete={onDeleteComment}
          />
        );
      }

      return (
        <SavedComment
          comment={comment}
          onEdit={() => onStartEdit(comment)}
          onDelete={onDeleteComment}
        />
      );
    },
    [fileComments, editingComment, activeInput, toolbarState, fileInfo.path, onAddComment, onUpdateComment, onDeleteComment, onCancelInput, onStartEdit, onToolbarComment, onToolbarSplit, fileSplitSelections, onDeleteSplitSelection],
  );

  const renderHeaderMetadata = useCallback(() => {
    if (!onToggleViewed) return null;
    return <ViewedToggle isViewed={false} onToggle={() => onToggleViewed(fileInfo.path)} />;
  }, [onToggleViewed, fileInfo.path]);

  return (
    <FileDiff<CommentMeta>
      key={
        shouldPreloadHighlightedDiff
          ? `${preloadKey}:${prerenderedDiffHTML == null ? 'plain' : 'preloaded'}`
          : fileInfo.path
      }
      fileDiff={fileDiff}
      options={options}
      lineAnnotations={lineAnnotations}
      renderAnnotation={renderAnnotation}
      selectedLines={selectedLines}
      renderHeaderMetadata={onToggleViewed ? renderHeaderMetadata : undefined}
      prerenderedHTML={prerenderedDiffHTML}
      disableWorkerPool={shouldPreloadHighlightedDiff}
    />
  );
});

const DIFF_THEMES: { group: string; themes: string[] }[] = [
  { group: 'Pierre', themes: ['pierre-dark', 'pierre-light'] },
  { group: 'GitHub', themes: ['github-dark', 'github-dark-default', 'github-dark-dimmed', 'github-dark-high-contrast', 'github-light', 'github-light-default', 'github-light-high-contrast'] },
  { group: 'Catppuccin', themes: ['catppuccin-frappe', 'catppuccin-latte', 'catppuccin-macchiato', 'catppuccin-mocha'] },
  { group: 'Material', themes: ['material-theme', 'material-theme-darker', 'material-theme-lighter', 'material-theme-ocean', 'material-theme-palenight'] },
  { group: 'Gruvbox', themes: ['gruvbox-dark-hard', 'gruvbox-dark-medium', 'gruvbox-dark-soft', 'gruvbox-light-hard', 'gruvbox-light-medium', 'gruvbox-light-soft'] },
  { group: 'Other', themes: ['andromeeda', 'aurora-x', 'ayu-dark', 'ayu-light', 'ayu-mirage', 'dark-plus', 'dracula', 'dracula-soft', 'everforest-dark', 'everforest-light', 'horizon', 'horizon-bright', 'houston', 'kanagawa-dragon', 'kanagawa-lotus', 'kanagawa-wave', 'laserwave', 'light-plus', 'min-dark', 'min-light', 'monokai', 'night-owl', 'night-owl-light', 'nord', 'one-dark-pro', 'one-light', 'plastic', 'poimandres', 'red', 'rose-pine', 'rose-pine-dawn', 'rose-pine-moon', 'slack-dark', 'slack-ochin', 'snazzy-light', 'solarized-dark', 'solarized-light', 'synthwave-84', 'tokyo-night', 'vesper', 'vitesse-black', 'vitesse-dark', 'vitesse-light'] },
];

function SettingsModal({
  isOpen,
  onClose,
  diffThemeDark,
  diffThemeLight,
  onDarkChange,
  onLightChange,
}: {
  isOpen: boolean;
  onClose: () => void;
  diffThemeDark: string;
  diffThemeLight: string;
  onDarkChange: (v: string) => void;
  onLightChange: (v: string) => void;
}) {
  if (!isOpen) return null;

  const themeSelect = (value: string, onChange: (v: string) => void) => (
    <select
      className="w-full bg-surface border border-border rounded-md px-2.5 py-1.5 font-mono text-[0.78rem] text-text-primary cursor-pointer"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {DIFF_THEMES.map((group) => (
        <optgroup key={group.group} label={group.group}>
          {group.themes.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </optgroup>
      ))}
    </select>
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-surface border border-border rounded-lg shadow-lg w-full max-w-[400px] mx-4">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h2 className="text-text-primary text-[0.95rem] font-semibold m-0">Settings</h2>
          <button
            className="appearance-none border-none bg-transparent text-text-muted hover:text-text-primary cursor-pointer p-1"
            onClick={onClose}
            type="button"
            aria-label="Close settings"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>
        <div className="px-5 py-4 flex flex-col gap-4">
          <div>
            <label className="block font-mono text-[0.72rem] font-medium text-text-secondary mb-1.5 uppercase tracking-[0.08em]">
              Dark Mode Diff Theme
            </label>
            {themeSelect(diffThemeDark, onDarkChange)}
          </div>
          <div>
            <label className="block font-mono text-[0.72rem] font-medium text-text-secondary mb-1.5 uppercase tracking-[0.08em]">
              Light Mode Diff Theme
            </label>
            {themeSelect(diffThemeLight, onLightChange)}
          </div>
        </div>
      </div>
    </div>
  );
}

function ChevronDownIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

function GitBranchIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="6" y1="3" x2="6" y2="15" />
      <circle cx="18" cy="6" r="3" />
      <circle cx="6" cy="18" r="3" />
      <path d="M18 9a9 9 0 0 1-9 9" />
    </svg>
  );
}

const WORKING_KEY = '__working__';

function diffItemId(key: string): string {
  return `sc-diff-item-${key.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
}

type StackListProps = {
  branches: StackBranch[];
  selection: DiffSelection | null;
  isStackLoading: boolean;
  isGithubInfoLoading: boolean;
  githubInfo: Record<string, GitHubBranchInfo>;
  parentIndexMap: Map<string, number>;
  lastChildIndexMap: Map<number, number>;
  onSelect: (sel: DiffSelection) => void;
  isFiltering: boolean;
  workingVisible: boolean;
  activeKey: string | null;
  onActivateKey: (key: string) => void;
};

function StackList({
  branches,
  selection,
  isStackLoading,
  isGithubInfoLoading,
  githubInfo,
  parentIndexMap,
  lastChildIndexMap,
  onSelect,
  isFiltering,
  workingVisible,
  activeKey,
  onActivateKey,
}: StackListProps) {
  const activeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest' });
  }, [activeKey]);

  const renderBranchButton = (branch: StackBranch, index: number | null) => {
    const active = selection?.type === 'branch' && branch.name === selection.name;
    const isActive = activeKey === branch.name;
    const branchPadding =
      index === null ? 8 : STACK_CARD_INDENT_BASE + branch.depth * STACK_CARD_INDENT_STEP;
    const ghInfo = githubInfo[branch.name];

    return (
      <button
        ref={isActive ? activeRef : undefined}
        id={diffItemId(branch.name)}
        role="option"
        aria-selected={active}
        className={`relative appearance-none rounded-md py-[5px] px-[7px] text-left text-text-primary cursor-pointer transition-[background] duration-150 ease-in-out border-none ${active ? 'bg-accent-bg' : isActive ? 'bg-surface-hover' : 'bg-transparent hover:bg-surface-hover'} ${isActive ? 'ring-1 ring-inset ring-accent/40' : ''}`}
        style={{
          ...(index === null ? {} : { anchorName: `--branch-${index}` }),
          marginInlineStart: `${branchPadding}px`,
          marginInlineEnd: '8px',
        } as React.CSSProperties}
        onClick={() => onSelect({ type: 'branch', name: branch.name })}
        onMouseMove={() => { if (!isActive) onActivateKey(branch.name); }}
        type="button"
      >
        <span className="relative z-[2] flex items-center gap-[7px] w-full min-w-0">
                <span className="min-w-0 flex-1 flex flex-col gap-[1px]">
                  <span className="flex items-center gap-[7px] min-w-0">
                    <span className="text-[0.88rem] font-semibold whitespace-nowrap overflow-hidden text-ellipsis">
                      {branch.name}
                    </span>
                    {branch.isCurrent && (
                      <span className="font-mono text-[0.58rem] font-medium uppercase tracking-[0.05em] text-accent bg-accent/10 border border-accent/18 ml-1.5 px-[5px] py-px rounded-full shrink-0 leading-[1.5]">
                        current
                      </span>
                    )}
                  </span>
                  <span
                    className="font-mono text-[0.62rem] text-text-muted whitespace-nowrap overflow-hidden text-ellipsis"
                    title={`${branch.commitShort} ${branch.commitSubject}`}
                  >
                    {branch.commitShort} {branch.commitSubject}
                  </span>
                </span>
                {isGithubInfoLoading ? (
                  <span className="ml-auto flex items-center gap-[5px] shrink-0">
                    <span className="inline-block w-[32px] h-[14px] rounded-full bg-surface-hover animate-pulse" />
                    <span className="inline-block w-[10px] h-[10px] rounded-full bg-surface-hover animate-pulse" />
                  </span>
                ) : (ghInfo?.prNumber != null || ghInfo?.checkStatus != null) ? (
                  <span className="ml-auto flex items-center gap-[5px] shrink-0">
                    {ghInfo?.prNumber != null && ghInfo.prUrl && (
                      <a
                        href={ghInfo.prUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className={`font-mono text-[0.58rem] font-medium no-underline px-[5px] py-px rounded-full leading-[1.5] border ${ghInfo.prIsDraft ? 'text-text-muted bg-surface-hover border-border' : 'text-green-400 bg-green-400/10 border-green-400/18'}`}
                      >
                        #{ghInfo.prNumber}
                      </a>
                    )}
                    {ghInfo?.checkStatus != null && (
                      <span
                        className="shrink-0 text-[0.7rem] leading-none"
                        title={`CI: ${ghInfo.checkStatus}`}
                      >
                        {ghInfo.checkStatus === 'success' && <span className="text-green-400">&#10003;</span>}
                        {ghInfo.checkStatus === 'failure' && <span className="text-red-400">&#10007;</span>}
                        {ghInfo.checkStatus === 'pending' && <span className="text-yellow-400">&#9679;</span>}
                      </span>
                    )}
                  </span>
                ) : null}
              </span>
            </button>
    );
  };

  const noResults = isFiltering && !workingVisible && branches.length === 0;

  return (
    <div
      id="sc-diff-listbox"
      className="relative flex flex-col gap-0 p-1.5 overflow-y-auto overflow-x-clip flex-1 min-h-0"
      role="listbox"
      aria-label="Tracked stack branches"
    >
      {isStackLoading ? (
        <p className="m-3 text-text-muted text-[0.82rem]">Loading stack…</p>
      ) : null}

      {!isStackLoading && !isFiltering && branches.length === 0 ? (
        <p className="m-3 text-text-muted text-[0.82rem]">
          No tracked branches found in this repository.
        </p>
      ) : null}

      {noResults ? (
        <p className="m-3 text-text-muted text-[0.82rem]">No matches.</p>
      ) : null}

      {workingVisible && (
        <button
          ref={activeKey === WORKING_KEY ? activeRef : undefined}
          id={diffItemId(WORKING_KEY)}
          role="option"
          aria-selected={selection?.type === 'working'}
          className={`relative appearance-none rounded-md py-[5px] px-[7px] mx-[8px] mb-1 text-left text-text-primary cursor-pointer transition-[background] duration-150 ease-in-out border-none ${selection?.type === 'working' ? 'bg-accent-bg' : activeKey === WORKING_KEY ? 'bg-surface-hover' : 'bg-transparent hover:bg-surface-hover'} ${activeKey === WORKING_KEY ? 'ring-1 ring-inset ring-accent/40' : ''}`}
          onClick={() => onSelect({ type: 'working' })}
          onMouseMove={() => { if (activeKey !== WORKING_KEY) onActivateKey(WORKING_KEY); }}
          type="button"
        >
          <span className="relative z-[2] flex items-center gap-[7px]">
            <span className="text-[0.82rem] font-semibold whitespace-nowrap overflow-hidden text-ellipsis">
              Working Changes
            </span>
            <span className="font-mono text-[0.58rem] font-medium uppercase tracking-[0.05em] text-text-muted bg-surface-hover border border-border px-[5px] py-px rounded-full shrink-0 leading-[1.5]">
              git diff
            </span>
          </span>
        </button>
      )}

      {!isFiltering && workingVisible && branches.length > 0 && (
        <div className="border-t border-border mx-2 my-1" />
      )}

      {isFiltering
        ? branches.map((branch) => (
            <React.Fragment key={branch.name}>
              {renderBranchButton(branch, null)}
            </React.Fragment>
          ))
        : branches.map((branch, index) => {
            const parentIndex = parentIndexMap.get(branch.parent) ?? -1;
            const lastChildIdx = lastChildIndexMap.get(index);

            return (
              <React.Fragment key={branch.name}>
                {renderBranchButton(branch, index)}
                {lastChildIdx !== undefined && (
                  <div
                    aria-hidden
                    className="stack-guide-vertical"
                    style={{
                      '--from': `--branch-${index}`,
                      '--to': `--branch-${lastChildIdx}`,
                      left: `${STACK_GUIDE_OFFSET + branch.depth * STACK_GUIDE_STEP}px`,
                    } as React.CSSProperties}
                  />
                )}
                {branch.depth > 0 && parentIndex >= 0 && (
                  <div
                    aria-hidden
                    className="stack-guide-horizontal"
                    style={{
                      '--at': `--branch-${index}`,
                      left: `${STACK_GUIDE_OFFSET + (branch.depth - 1) * STACK_GUIDE_STEP}px`,
                      width: '10px',
                    } as React.CSSProperties}
                  />
                )}
              </React.Fragment>
            );
          })}
    </div>
  );
}

type DiffSwitcherProps = Omit<
  StackListProps,
  'isFiltering' | 'workingVisible' | 'activeKey' | 'onActivateKey'
> & {
  diff: DiffResponse | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

const IS_MAC = typeof navigator !== 'undefined' && navigator.platform.includes('Mac');

function DiffSwitcher({ diff, open, onOpenChange, ...stackProps }: DiffSwitcherProps) {
  const { selection, branches, onSelect } = stackProps;
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) setQuery('');
  }, [open]);

  const isWorking = selection?.type === 'working';
  const targetMain = isWorking
    ? 'Uncommitted changes'
    : diff
      ? diff.branch
      : selection?.type === 'branch'
        ? selection.name
        : 'Select a branch';
  const targetParent = !isWorking && diff ? diff.parent : null;
  const chevronCls = `text-text-muted shrink-0 transition-transform duration-150 ${open ? 'rotate-180' : ''}`;

  const q = query.trim().toLowerCase();
  const isFiltering = q !== '';
  const workingVisible =
    !isFiltering || 'working changes'.includes(q) || 'uncommitted changes'.includes(q);
  const filteredBranches = isFiltering
    ? branches.filter(
        (b) => b.name.toLowerCase().includes(q) || b.commitSubject.toLowerCase().includes(q),
      )
    : branches;

  // Flat list of selectable rows, in the same order StackList renders them.
  const items: DiffSelection[] = [
    ...(workingVisible ? [{ type: 'working' as const }] : []),
    ...filteredBranches.map((b) => ({ type: 'branch' as const, name: b.name })),
  ];
  const clampedActive = items.length === 0 ? -1 : Math.min(activeIndex, items.length - 1);
  const activeItem = clampedActive >= 0 ? items[clampedActive] : undefined;
  const activeKey = activeItem
    ? activeItem.type === 'working'
      ? WORKING_KEY
      : activeItem.name
    : null;
  const activeId = activeKey ? diffItemId(activeKey) : undefined;

  // Reset the highlight to the top whenever the result set changes.
  useEffect(() => {
    setActiveIndex(0);
  }, [query, open]);

  const activateKey = (key: string) => {
    const idx = items.findIndex(
      (it) => (it.type === 'working' ? WORKING_KEY : it.name) === key,
    );
    if (idx >= 0) setActiveIndex(idx);
  };

  const handleInputKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (items.length > 0) setActiveIndex((i) => Math.min(i + 1, items.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (items.length > 0) setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Home') {
      e.preventDefault();
      setActiveIndex(0);
    } else if (e.key === 'End') {
      e.preventDefault();
      if (items.length > 0) setActiveIndex(items.length - 1);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (activeItem) onSelect(activeItem);
    }
  };

  return (
    <Popover.Root open={open} onOpenChange={onOpenChange}>
      <Popover.Trigger
        className="group flex items-center min-w-0 max-w-full appearance-none border-none bg-transparent p-0 m-0 text-left cursor-pointer rounded-md focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/30"
        aria-label="Switch diff"
      >
        <span className="inline-flex items-center gap-2.5 border border-border rounded-md bg-surface-hover px-2.5 py-1.5 min-w-0 max-w-full group-hover:bg-surface-active group-hover:border-border-strong transition-colors duration-100">
          <span className="text-accent shrink-0"><GitBranchIcon /></span>
          <span className="flex flex-col items-start min-w-0 leading-tight">
            <span className="font-mono text-[0.56rem] font-medium uppercase tracking-[0.12em] text-text-muted">
              Viewing diff
            </span>
            <span className="flex items-center min-w-0 max-w-full text-[0.92rem] font-bold text-text-primary truncate">
              {targetMain}
              {targetParent && (
                <>
                  <span className="text-text-muted font-normal mx-1">&rarr;</span>
                  <span className="text-text-secondary font-semibold">{targetParent}</span>
                </>
              )}
            </span>
          </span>
          <kbd className="hidden sm:inline-flex items-center gap-0.5 ml-1 font-mono text-[0.58rem] font-medium text-text-muted bg-surface border border-border rounded px-1 py-px leading-none shrink-0">
            {IS_MAC ? '⌘' : 'Ctrl'}K
          </kbd>
          <span className={`${chevronCls} ml-0.5`}><ChevronDownIcon /></span>
        </span>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner side="bottom" align="start" sideOffset={10} className="z-50">
          <Popover.Popup
            initialFocus={inputRef}
            className="w-[340px] max-h-[min(560px,70vh)] flex flex-col bg-surface border border-border rounded-lg shadow-lg overflow-hidden outline-none origin-[var(--transform-origin)] transition-[opacity,transform] duration-150 data-[starting-style]:opacity-0 data-[starting-style]:scale-[0.98] data-[ending-style]:opacity-0 data-[ending-style]:scale-[0.98]"
          >
            <div className="px-3.5 py-2.5 border-b border-border flex items-center justify-between shrink-0">
              <span className="font-mono text-[0.62rem] font-medium uppercase tracking-[0.13em] text-accent">
                Switch diff
              </span>
              {branches.length > 0 && (
                <span className="font-mono text-[0.6rem] text-text-muted bg-surface-hover border border-border px-2 py-[2px] rounded-full">
                  {isFiltering ? `${filteredBranches.length}/${branches.length}` : branches.length} branch{branches.length === 1 ? '' : 'es'}
                </span>
              )}
            </div>
            {branches.length > 0 && (
              <div className="px-2.5 py-2 border-b border-border shrink-0">
                <input
                  ref={inputRef}
                  type="text"
                  role="combobox"
                  aria-expanded="true"
                  aria-controls="sc-diff-listbox"
                  aria-activedescendant={activeId}
                  autoComplete="off"
                  className="w-full appearance-none border border-border rounded-md bg-surface-hover text-text-primary font-mono text-[0.72rem] px-2.5 py-1.5 outline-none transition-[border-color] duration-150 ease-in-out focus:border-accent/40 focus:ring-1 focus:ring-accent/10 placeholder:text-text-muted"
                  placeholder="Filter branches…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={handleInputKeyDown}
                />
              </div>
            )}
            <StackList
              {...stackProps}
              branches={filteredBranches}
              isFiltering={isFiltering}
              workingVisible={workingVisible}
              activeKey={activeKey}
              onActivateKey={activateKey}
            />
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}

export default function App() {
  const isWideScreen = useMediaQuery('(min-width: 961px)');
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [stack, setStack] = useState<StackResponse | null>(null);
  const [selection, setSelectionRaw] = useState<DiffSelection | null>(
    () => selectionFromHash(window.location.hash),
  );
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [workingPatch, setWorkingPatch] = useState<string | null>(null);
  const [isStackLoading, setIsStackLoading] = useState(true);
  const [isDiffLoading, setIsDiffLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [diffStyle, setDiffStyle] = useState<DiffStyle>('unified');
  const [fileFilter, setFileFilter] = useState('');
  const [activeFileIndex, setActiveFileIndex] = useState<number | null>(null);
  const diffContentRef = useRef<HTMLDivElement>(null);
  const fileRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const fileScrollCleanupRef = useRef<(() => void) | null>(null);
  const [comments, setComments] = useState<DiffComment[]>([]);
  const [activeInput, setActiveInput] = useState<ActiveInput>(null);
  const [editingComment, setEditingComment] = useState<DiffComment | null>(null);
  const [copyFeedback, setCopyFeedback] = useState(false);
  const [toolbarState, setToolbarState] = useState<ActiveInput>(null);
  const [moveSuccess, setMoveSuccess] = useState<string | null>(null);
  const [splitLineSelections, setSplitLineSelections] = useState<SplitLineSelection[]>([]);
  const [showSplitLinesDialog, setShowSplitLinesDialog] = useState(false);
  const [isSplittingLines, setIsSplittingLines] = useState(false);
  const [splitLinesError, setSplitLinesError] = useState<string | null>(null);
  const [viewedFiles, setViewedFiles] = useState<Set<string>>(new Set());
  const [expandedLargeFiles, setExpandedLargeFiles] = useState<Set<string>>(new Set());
  const [githubInfo, setGithubInfo] = useState<Record<string, GitHubBranchInfo>>({});
  const [isGithubInfoLoading, setIsGithubInfoLoading] = useState(true);
  const [isReviewDialogOpen, setIsReviewDialogOpen] = useState(false);
  const [reviewModelStatus, setReviewModelStatus] = useState<Map<string, 'pending' | 'done' | 'error'>>(new Map());
  const [reviewModels, setReviewModels] = useState<ReviewModel[]>([]);
  const [reviewSummaries, setReviewSummaries] = useState<Map<string, string>>(new Map());
  const [reviewFixPrompt, setReviewFixPrompt] = useState<string | null>(null);
  const isReviewing = reviewModelStatus.size > 0 && [...reviewModelStatus.values()].some((s) => s === 'pending');

  const setSelection = useCallback((sel: DiffSelection | null) => {
    setSelectionRaw(sel);
    if (sel) {
      const newHash = selectionToHash(sel);
      if (window.location.hash !== newHash) {
        window.history.pushState(null, '', newHash);
      }
    }
  }, []);

  useEffect(() => {
    const onHashChange = () => {
      const sel = selectionFromHash(window.location.hash);
      if (sel) setSelectionRaw(sel);
    };
    window.addEventListener('hashchange', onHashChange);
    window.addEventListener('popstate', onHashChange);
    return () => {
      window.removeEventListener('hashchange', onHashChange);
      window.removeEventListener('popstate', onHashChange);
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setSwitcherOpen((prev) => !prev);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const expandLargeFile = useCallback((path: string) => {
    setExpandedLargeFiles((prev) => {
      const next = new Set(prev);
      next.add(path);
      return next;
    });
  }, []);

  const [themeMode, setThemeMode] = useState<ThemeMode>(
    () => (localStorage.getItem('shortcake-theme') as ThemeMode) ?? 'dark',
  );
  const prefersDark = useMediaQuery('(prefers-color-scheme: dark)');
  const resolvedTheme: 'dark' | 'light' =
    themeMode === 'system' ? (prefersDark ? 'dark' : 'light') : themeMode;

  const [diffThemeDark, setDiffThemeDark] = useState<string>(
    () => localStorage.getItem('shortcake-diff-theme-dark') ?? 'pierre-dark',
  );
  const [diffThemeLight, setDiffThemeLight] = useState<string>(
    () => localStorage.getItem('shortcake-diff-theme-light') ?? 'pierre-light',
  );
  const [showSettings, setShowSettings] = useState(false);

  const activeDiffTheme = resolvedTheme === 'light' ? diffThemeLight : diffThemeDark;

  useEffect(() => {
    localStorage.setItem('shortcake-theme', themeMode);
    document.documentElement.dataset.theme = resolvedTheme;
  }, [themeMode, resolvedTheme]);

  useEffect(() => {
    localStorage.setItem('shortcake-diff-theme-dark', diffThemeDark);
  }, [diffThemeDark]);

  useEffect(() => {
    localStorage.setItem('shortcake-diff-theme-light', diffThemeLight);
  }, [diffThemeLight]);

  useEffect(() => {
    let cancelled = false;

    const loadStack = async () => {
      setIsStackLoading(true);
      setError(null);
      try {
        const data = await fetchJSON<StackResponse>('/api/stack');
        if (cancelled) return;

        setStack(data);

        // If we already have a selection from the URL hash, keep it
        // (but validate branch selections still exist)
        const hashSel = selectionFromHash(window.location.hash);
        if (hashSel) {
          if (hashSel.type === 'working') {
            setSelection(hashSel);
          } else if (data.branches.some((b) => b.name === hashSel.name)) {
            setSelection(hashSel);
          } else {
            // Branch from hash no longer exists, default to working
            setSelection({ type: 'working' });
          }
        } else {
          // No hash — default to working changes
          setSelection({ type: 'working' });
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load stack');
        }
      } finally {
        if (!cancelled) setIsStackLoading(false);
      }
    };

    void loadStack();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!selection) {
      setDiff(null);
      setWorkingPatch(null);
      return;
    }

    let cancelled = false;

    const loadDiff = async () => {
      setIsDiffLoading(true);
      setError(null);
      try {
        if (selection.type === 'working') {
          const data = await fetchJSON<WorkingDiffResponse>('/api/diff/working');
          if (!cancelled) {
            setWorkingPatch(data.patch);
            setDiff(null);
          }
        } else {
          const data = await fetchJSON<DiffResponse>(
            `/api/diff?branch=${encodeURIComponent(selection.name)}`,
          );
          if (!cancelled) {
            setDiff(data);
            setWorkingPatch(null);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load diff');
          setDiff(null);
          setWorkingPatch(null);
        }
      } finally {
        if (!cancelled) setIsDiffLoading(false);
      }
    };

    void loadDiff();
    return () => { cancelled = true; };
  }, [selection]);

  useEffect(() => {
    setFileFilter('');
    setActiveFileIndex(null);
    fileRefs.current = {};
    setComments([]);
    setActiveInput(null);
    setEditingComment(null);
    setToolbarState(null);
    setMoveSuccess(null);
    setSplitLineSelections([]);
    setShowSplitLinesDialog(false);
    setSplitLinesError(null);
    setViewedFiles(new Set());
    setIsReviewDialogOpen(false);
    setReviewModelStatus(new Map());
    setReviewSummaries(new Map());
    setReviewFixPrompt(null);
  }, [selection]);

  const activePatch = selection?.type === 'working' ? workingPatch : diff?.patch;

  const diffPatches = useMemo(
    () => splitPatchIntoFiles(activePatch ?? ''),
    [activePatch],
  );

  const fileInfos = useMemo(
    () => diffPatches.map((patch, i) => parseFileInfo(patch, i)),
    [diffPatches],
  );

  const commentsByFile = useMemo(() => {
    const map = new Map<string, DiffComment[]>();
    for (const c of comments) {
      let arr = map.get(c.file);
      if (!arr) { arr = []; map.set(c.file, arr); }
      arr.push(c);
    }
    return map;
  }, [comments]);

  const splitSelectionsByFile = useMemo(() => {
    const map = new Map<string, SplitLineSelection[]>();
    for (const s of splitLineSelections) {
      let arr = map.get(s.file);
      if (!arr) { arr = []; map.set(s.file, arr); }
      arr.push(s);
    }
    return map;
  }, [splitLineSelections]);

  const toggleViewed = useCallback((path: string) => {
    setViewedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const alignFileInDiffPane = useCallback((index: number): boolean => {
    const scroller = diffContentRef.current;
    const target = fileRefs.current[index];
    if (!scroller || !target) return false;

    const scrollerRect = scroller.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    scroller.scrollTo({
      top: scroller.scrollTop + targetRect.top - scrollerRect.top,
      behavior: 'auto',
    });
    return true;
  }, []);

  const scrollToFile = useCallback((index: number) => {
    const info = fileInfos[index];
    if (info && viewedFiles.has(info.path)) {
      setViewedFiles((prev) => {
        const next = new Set(prev);
        next.delete(info.path);
        return next;
      });
    }
    setActiveFileIndex(index);

    fileScrollCleanupRef.current?.();
    fileScrollCleanupRef.current = null;

    requestAnimationFrame(() => {
      if (!alignFileInDiffPane(index) || typeof ResizeObserver === 'undefined') {
        return;
      }

      let frameId: number | null = null;
      let timeoutId: number;
      const observer = new ResizeObserver(() => {
        if (frameId !== null) cancelAnimationFrame(frameId);
        frameId = requestAnimationFrame(() => {
          frameId = null;
          alignFileInDiffPane(index);
        });
      });
      const cleanup = () => {
        observer.disconnect();
        if (frameId !== null) cancelAnimationFrame(frameId);
        window.clearTimeout(timeoutId);
        if (fileScrollCleanupRef.current === cleanup) {
          fileScrollCleanupRef.current = null;
        }
      };

      for (let i = 0; i <= index; i++) {
        const section = fileRefs.current[i];
        if (section) observer.observe(section);
      }
      timeoutId = window.setTimeout(cleanup, 600);
      fileScrollCleanupRef.current = cleanup;
    });
  }, [alignFileInDiffPane, fileInfos, viewedFiles]);

  useEffect(() => {
    return () => {
      fileScrollCleanupRef.current?.();
    };
  }, []);

  const handleRangeSelected = useCallback(
    (file: string, startLine: number, endLine: number, side: AnnotationSide) => {
      setEditingComment(null);
      setActiveInput(null);
      // Always show toolbar (for both branch diffs and working changes)
      setToolbarState({ file, startLine, endLine, side });
    },
    [],
  );

  const handleStartEdit = useCallback((comment: DiffComment) => {
    setEditingComment(comment);
    setActiveInput({ file: comment.file, startLine: comment.startLine, endLine: comment.endLine, side: comment.side });
  }, []);

  const handleAddComment = useCallback(
    (file: string, startLine: number, endLine: number, side: AnnotationSide, text: string) => {
      const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      setComments((prev) => [...prev, { id, file, startLine, endLine, side, text }]);
      setActiveInput(null);
      setEditingComment(null);
    },
    [],
  );

  const handleUpdateComment = useCallback((id: string, text: string) => {
    setComments((prev) =>
      prev.map((c) => (c.id === id ? { ...c, text } : c)),
    );
    setActiveInput(null);
    setEditingComment(null);
  }, []);

  const handleDeleteComment = useCallback((id: string) => {
    setComments((prev) => prev.filter((c) => c.id !== id));
  }, []);

  const handleCancelInput = useCallback(() => {
    setActiveInput(null);
    setEditingComment(null);
    setToolbarState(null);
  }, []);

  const handleToolbarComment = useCallback(() => {
    if (!toolbarState) return;
    setActiveInput(toolbarState);
    setToolbarState(null);
  }, [toolbarState]);

  const handleToolbarSplit = useCallback(() => {
    if (!toolbarState) return;
    const fileIndex = fileInfos.findIndex((f) => f.path === toolbarState.file);
    const filePatch = diffPatches[fileIndex] ?? '';
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    setSplitLineSelections((prev) => [
      ...prev,
      {
        id,
        file: toolbarState.file,
        startLine: toolbarState.startLine,
        endLine: toolbarState.endLine,
        side: toolbarState.side,
        filePatch,
      },
    ]);
    setToolbarState(null);
  }, [toolbarState, fileInfos, diffPatches]);

  const handleDeleteSplitSelection = useCallback((id: string) => {
    setSplitLineSelections((prev) => prev.filter((s) => s.id !== id));
  }, []);

  const refreshData = useCallback(async (knownStack?: StackResponse) => {
    try {
      const stackData = knownStack ?? await fetchJSON<StackResponse>('/api/stack');
      setStack(stackData);
      if (selection?.type === 'branch') {
        const diffData = await fetchJSON<DiffResponse>(
          `/api/diff?branch=${encodeURIComponent(selection.name)}`,
        );
        setDiff(diffData);
      } else if (selection?.type === 'working') {
        const data = await fetchJSON<WorkingDiffResponse>('/api/diff/working');
        setWorkingPatch(data.patch);
      }
    } catch {
      // Silently fail refresh — data may be stale but still usable
    }
  }, [selection]);

  const handleSplitLinesSubmit = useCallback(
    async (commitMessage: string) => {
      if (splitLineSelections.length === 0 || selection?.type !== 'branch') return;

      setIsSplittingLines(true);
      setSplitLinesError(null);

      try {
        const result = await postJSON<SplitLinesResponse>('/api/split-lines', {
          sourceBranch: selection.name,
          chunks: [
            {
              commitMessage,
              selections: splitLineSelections.map((s) => ({
                filePath: s.file,
                filePatch: s.filePatch,
                startLine: s.startLine,
                endLine: s.endLine,
                side: s.side,
              })),
            },
          ],
        });

        setSplitLineSelections([]);
        setShowSplitLinesDialog(false);
        setMoveSuccess(`Split into ${result.newBranches.join(', ')}`);
        setTimeout(() => setMoveSuccess(null), 3000);

        await refreshData();
      } catch (err) {
        setSplitLinesError(err instanceof Error ? err.message : 'Split failed');
      } finally {
        setIsSplittingLines(false);
      }
    },
    [splitLineSelections, selection, refreshData],
  );

  const handleSplitLinesCancel = useCallback(() => {
    setShowSplitLinesDialog(false);
    setSplitLinesError(null);
  }, []);

  // Poll for external stack and working tree changes every 3 seconds
  const lastStackKeyRef = useRef<string>('');
  const lastWorkingDiffKeyRef = useRef<string>('');
  const selectionRef = useRef<DiffSelection | null>(selection);
  selectionRef.current = selection;
  const refreshDataRef = useRef(refreshData);
  refreshDataRef.current = refreshData;

  useEffect(() => {
    if (stack) {
      lastStackKeyRef.current = stackPollKey(stack);
    }
  }, [stack]);

  useEffect(() => {
    let intervalId: ReturnType<typeof setInterval> | null = null;

    const startPolling = () => {
      if (intervalId) return;
      intervalId = setInterval(async () => {
        if (isSplittingLines) return;
        try {
          const data = await fetchJSON<UIStateResponse>('/api/state');
          const newStackKey = stackPollKey(data);
          const stackChanged = newStackKey !== lastStackKeyRef.current;
          const previousWorkingDiffKey = lastWorkingDiffKeyRef.current;
          const workingDiffChanged =
            data.workingDiffKey !== previousWorkingDiffKey &&
            selectionRef.current?.type === 'working';

          lastWorkingDiffKeyRef.current = data.workingDiffKey;

          if (stackChanged || workingDiffChanged) {
            await refreshDataRef.current(data);
          }
        } catch {
          // Silent failure — will retry on next poll
        }
      }, 3000);
    };

    const stopPolling = () => {
      if (intervalId) {
        clearInterval(intervalId);
        intervalId = null;
      }
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        startPolling();
      } else {
        stopPolling();
      }
    };

    if (document.visibilityState === 'visible') {
      startPolling();
    }

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      stopPolling();
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [isSplittingLines]);

  // Fetch GitHub info (PR links + CI status) on a slower polling interval
  useEffect(() => {
    let cancelled = false;

    const fetchGithubInfo = async () => {
      try {
        const data = await fetchJSON<GitHubInfoResponse>('/api/github-info');
        if (!cancelled) {
          setGithubInfo(data.branches);
          setIsGithubInfoLoading(false);
        }
      } catch {
        // Silent failure — GitHub info is optional
        if (!cancelled) setIsGithubInfoLoading(false);
      }
    };

    fetchGithubInfo();

    const intervalId = setInterval(fetchGithubInfo, 30_000);
    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, []);

  const handleCopyComments = useCallback(() => {
    if (comments.length === 0) return;
    const markdown = comments
      .map((c) => {
        const ref = `\`${formatLineRef(c.file, c.startLine, c.endLine)}\``;
        const prefix = c.source?.type === 'ai' ? `[${c.source.model}] ` : '';
        return `- ${ref} - ${prefix}${c.text}`;
      })
      .join('\n');
    void navigator.clipboard.writeText(markdown).then(() => {
      setCopyFeedback(true);
      setTimeout(() => setCopyFeedback(false), 2000);
    });
  }, [comments]);

  const aiCommentCount = useMemo(
    () => comments.filter((c) => c.source?.type === 'ai').length,
    [comments],
  );

  const handleOpenReviewDialog = useCallback(async () => {
    try {
      const data = await fetchJSON<{ models: ReviewModel[] }>('/api/review/models');
      setReviewModels(data.models);
      setIsReviewDialogOpen(true);
    } catch {
      setReviewModels([
        { id: 'claude', name: 'Claude', tool: 'claude', available: false },
        { id: 'codex', name: 'Codex', tool: 'codex', available: false },
      ]);
      setIsReviewDialogOpen(true);
    }
  }, []);

  const handleStartReview = useCallback(
    async (selectedModels: string[], synthesizeWith: string | null) => {
      if (!selection) return;
      const statusMap = new Map(selectedModels.map((m) => [m, 'pending' as const]));
      if (synthesizeWith) statusMap.set('synthesis', 'pending');
      setReviewModelStatus(statusMap);
      setIsReviewDialogOpen(false);

      try {
        const response = await fetch(`${API_BASE}/api/review`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            branch: selection.type === 'working' ? '__working__' : selection.name,
            models: selectedModels,
            synthesize: synthesizeWith,
          }),
        });

        if (!response.ok || !response.body) {
          setReviewModelStatus(new Map(selectedModels.map((m) => [m, 'error'])));
          return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';

          let eventType = '';
          for (const line of lines) {
            if (line.startsWith('event: ')) {
              eventType = line.slice(7).trim();
            } else if (line.startsWith('data: ') && (eventType === 'review' || eventType === 'synthesis')) {
              try {
                const data = JSON.parse(line.slice(6));
                const modelId: string = data.model;
                const isSynthesis = eventType === 'synthesis';
                const statusKey = isSynthesis ? 'synthesis' : modelId;
                // Mark model as done or error
                setReviewModelStatus((prev) => {
                  const next = new Map(prev);
                  next.set(statusKey, data.error ? 'error' : 'done');
                  return next;
                });
                const summaryLabel = isSynthesis ? `synthesis (${modelId})` : modelId;
                if (data.summary) {
                  setReviewSummaries((prev) => {
                    const next = new Map(prev);
                    next.set(summaryLabel, data.summary);
                    return next;
                  });
                }
                if (data.comments && Array.isArray(data.comments)) {
                  const sourceModel = isSynthesis ? `synthesis:${modelId}` : modelId;
                  const newComments: DiffComment[] = data.comments.map(
                    (c: { file: string; start_line: number; end_line: number; side: string; text: string; severity: string }) => ({
                      id: `ai-${sourceModel}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
                      file: c.file,
                      startLine: c.start_line,
                      endLine: c.end_line,
                      side: (c.side === 'deletions' ? 'deletions' : 'additions') as AnnotationSide,
                      text: c.text,
                      source: { type: 'ai' as const, model: sourceModel, severity: c.severity },
                    }),
                  );
                  if (isSynthesis) {
                    // Replace individual AI comments with synthesis-only
                    setComments((prev) => [
                      ...prev.filter((c) => !c.source || c.source.model.startsWith('synthesis:')),
                      ...newComments,
                    ]);
                    // Add synthesis summary alongside individual ones
                    setReviewSummaries((prev) => {
                      const next = new Map(prev);
                      next.set(summaryLabel, data.summary ?? '');
                      return next;
                    });
                    if (data.fix_prompt) {
                      setReviewFixPrompt(data.fix_prompt);
                    }
                  } else {
                    setComments((prev) => [...prev, ...newComments]);
                  }
                }
              } catch {
                // Skip malformed SSE data
              }
              eventType = '';
            }
          }
        }
      } catch {
        setReviewModelStatus((prev) => {
          const next = new Map(prev);
          for (const [k, v] of next) {
            if (v === 'pending') next.set(k, 'error');
          }
          return next;
        });
      }
    },
    [selection],
  );

  const branches = stack?.branches ?? [];

  const parentIndexMap = useMemo(() => {
    const map = new Map<string, number>();
    branches.forEach((b, i) => map.set(b.name, i));
    return map;
  }, [branches]);

  const lastChildIndexMap = useMemo(() => {
    const map = new Map<number, number>();
    for (let i = 0; i < branches.length; i++) {
      const parentIdx = parentIndexMap.get(branches[i]!.parent);
      if (parentIdx !== undefined) {
        map.set(parentIdx, i);
      }
    }
    return map;
  }, [branches, parentIndexMap]);

  return (
    <WorkerPoolContextProvider
      poolOptions={{ workerFactory: () => new DiffsWorker(), poolSize: 4 }}
      highlighterOptions={{}}
    >
    <main className="relative h-screen animate-fade-in overflow-hidden flex flex-col">
      <section className="bg-surface overflow-hidden flex flex-col min-w-0 flex-1 min-h-0">
        <SettingsModal
          isOpen={showSettings}
          onClose={() => setShowSettings(false)}
          diffThemeDark={diffThemeDark}
          diffThemeLight={diffThemeLight}
          onDarkChange={setDiffThemeDark}
          onLightChange={setDiffThemeLight}
        />
        <header className="px-[1.15rem] h-[60px] shrink-0 border-b border-border flex justify-between items-center gap-4">
          <DiffSwitcher
            diff={diff}
            open={switcherOpen}
            onOpenChange={setSwitcherOpen}
            selection={selection}
            branches={branches}
            isStackLoading={isStackLoading}
            isGithubInfoLoading={isGithubInfoLoading}
            githubInfo={githubInfo}
            parentIndexMap={parentIndexMap}
            lastChildIndexMap={lastChildIndexMap}
            onSelect={(sel) => { setSelection(sel); setSwitcherOpen(false); }}
          />

          <div className="flex items-center gap-2 shrink-0">
            {moveSuccess && (
              <span className="font-mono text-[0.7rem] text-accent whitespace-nowrap">
                {moveSuccess}
              </span>
            )}
            {selection && !isDiffLoading && diffPatches.length > 0 && (
              isReviewing ? (
                <div className="flex items-center gap-1.5 border border-border rounded-md px-2.5 py-1">
                  {[...reviewModelStatus.entries()].map(([model, status]) => {
                    const label = model.includes(':') ? model.split(':')[1] : model;
                    return (
                      <span key={model} className="flex items-center gap-1 font-mono text-[0.65rem] whitespace-nowrap">
                        {status === 'pending' && <span className="inline-block w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse" />}
                        {status === 'done' && <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-400" />}
                        {status === 'error' && <span className="inline-block w-1.5 h-1.5 rounded-full bg-red-400" />}
                        <span className={status === 'pending' ? 'text-text-muted' : status === 'done' ? 'text-green-400' : 'text-red-400'}>{label}</span>
                      </span>
                    );
                  })}
                </div>
              ) : (
                <button
                  type="button"
                  className="appearance-none border border-border bg-transparent text-text-secondary text-[0.7rem] font-mono px-2.5 py-1 rounded-md cursor-pointer hover:bg-surface-hover hover:text-text-primary transition-colors duration-100 whitespace-nowrap"
                  onClick={handleOpenReviewDialog}
                >
                  {aiCommentCount > 0 ? `Review (${aiCommentCount})` : 'Review'}
                </button>
              )
            )}
            {comments.length > 0 && (
              <button
                type="button"
                className="appearance-none border border-accent bg-accent/10 text-accent text-[0.7rem] font-mono px-2.5 py-1 rounded-md cursor-pointer hover:bg-accent/20 transition-colors duration-100 whitespace-nowrap"
                onClick={handleCopyComments}
              >
                {copyFeedback ? 'Copied!' : `Copy ${comments.length} comment${comments.length === 1 ? '' : 's'}`}
              </button>
            )}
            {!isDiffLoading && diffPatches.length > 0 && viewedFiles.size > 0 && (
              <span className="font-mono text-[0.68rem] text-accent bg-accent/10 border border-accent/20 px-2 py-[3px] rounded-full whitespace-nowrap">
                {viewedFiles.size}/{diffPatches.length} viewed
              </span>
            )}
            {!isDiffLoading && diffPatches.length > 0 && (
              <span className="font-mono text-[0.68rem] text-text-secondary bg-surface-hover border border-border px-2 py-[3px] rounded-full whitespace-nowrap">
                {diffPatches.length} file{diffPatches.length === 1 ? '' : 's'}
              </span>
            )}
            <div
              className="flex bg-surface-hover border border-border rounded-md p-0.5"
              role="group"
              aria-label="Theme"
            >
              {(['dark', 'light', 'system'] as const).map((mode) => (
                <button
                  key={mode}
                  className={`appearance-none border-none rounded-[6px] font-mono text-[0.7rem] tracking-[0.02em] px-2.5 py-1 cursor-pointer transition-[color,background] duration-[120ms] ease-in-out capitalize ${themeMode === mode ? 'text-text-primary bg-surface-active' : 'bg-transparent text-text-muted hover:text-text-secondary'}`}
                  onClick={() => setThemeMode(mode)}
                  type="button"
                >
                  {mode}
                </button>
              ))}
            </div>
            <div
              className="flex bg-surface-hover border border-border rounded-md p-0.5"
              role="group"
              aria-label="Diff layout"
            >
              <button
                className={`appearance-none border-none rounded-[6px] font-mono text-[0.7rem] tracking-[0.02em] px-2.5 py-1 cursor-pointer transition-[color,background] duration-[120ms] ease-in-out ${diffStyle === 'unified' ? 'text-text-primary bg-surface-active' : 'bg-transparent text-text-muted hover:text-text-secondary'}`}
                onClick={() => setDiffStyle('unified')}
                type="button"
              >
                Unified
              </button>
              <button
                className={`appearance-none border-none rounded-[6px] font-mono text-[0.7rem] tracking-[0.02em] px-2.5 py-1 cursor-pointer transition-[color,background] duration-[120ms] ease-in-out ${diffStyle === 'split' ? 'text-text-primary bg-surface-active' : 'bg-transparent text-text-muted hover:text-text-secondary'}`}
                onClick={() => setDiffStyle('split')}
                type="button"
              >
                Split
              </button>
            </div>
            <button
              className="appearance-none border-none bg-transparent text-text-muted hover:text-text-primary cursor-pointer p-1 transition-colors duration-100"
              onClick={() => setShowSettings(true)}
              type="button"
              aria-label="Settings"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
            </button>
          </div>
        </header>

        {error ? (
          <p className="m-[1.15rem] text-danger text-[0.88rem]">{error}</p>
        ) : null}

        {isDiffLoading ? (
          <p className="m-[1.15rem] text-text-muted text-[0.88rem]">Loading diff…</p>
        ) : null}

        {!isDiffLoading && activePatch !== undefined && activePatch !== null && activePatch.trim() === '' ? (
          <p className="m-[1.15rem] text-text-muted text-[0.88rem]">
            {selection?.type === 'working'
              ? 'No uncommitted changes.'
              : 'No file differences between this branch and its parent.'}
          </p>
        ) : null}

        {!isDiffLoading && activePatch && activePatch.trim() !== '' && diffPatches.length === 0 ? (
          <p className="m-[1.15rem] text-danger text-[0.88rem]">
            Could not render this diff patch.
          </p>
        ) : null}

        {!isDiffLoading && activePatch && diffPatches.length > 0 && (
          <div className="flex flex-1 min-h-0">
            {isWideScreen && (
            <aside className="w-[280px] min-w-[280px] border-r border-border flex flex-col overflow-hidden max-[1100px]:hidden">
              <div className="px-4 py-3 border-b border-border">
                <div className="flex items-center justify-between">
                  <span className="text-[0.8rem] font-semibold text-text-primary">
                    Files changed
                  </span>
                  <span className="text-[0.65rem] font-mono text-text-muted bg-surface-hover px-2 py-0.5 rounded-full">
                    {fileInfos.length}
                  </span>
                </div>
                {fileInfos.length > 0 && (
                  <div className="flex gap-2.5 mt-1.5 text-[0.65rem] font-mono">
                    <span className="text-stat-add">
                      +{fileInfos.reduce((s, f) => s + f.additions, 0)}
                    </span>
                    <span className="text-stat-del">
                      -{fileInfos.reduce((s, f) => s + f.deletions, 0)}
                    </span>
                  </div>
                )}
              </div>
              <ChangedFilesTree
                fileInfos={fileInfos}
                fileFilter={fileFilter}
                activeFileIndex={activeFileIndex}
                viewedFiles={viewedFiles}
                resolvedTheme={resolvedTheme}
                onFilterChange={setFileFilter}
                onFileClick={scrollToFile}
              />
            </aside>
            )}

            <div ref={diffContentRef} className="diff-content flex-1 min-w-0 overflow-auto">
              {reviewSummaries.size > 0 && (
                <ReviewSummaryPanel
                  summaries={reviewSummaries}
                  fixPrompt={reviewFixPrompt}
                  onClose={() => { setReviewSummaries(new Map()); setReviewFixPrompt(null); }}
                />
              )}
              {diffPatches.map((patch, index) => {
                const info = fileInfos[index];
                if (!info) return null;
                const isViewed = viewedFiles.has(info.path);
                return (
                  <div
                    className={index > 0 ? 'border-t-2 border-guide' : undefined}
                    key={`${selection?.type === 'working' ? 'working' : diff?.branch}-${index}`}
                    data-file-path={info.path}
                    data-file-index={index}
                    ref={(el) => { fileRefs.current[index] = el; }}
                  >
                    {isViewed ? (
                      <ViewedFileHeader fileInfo={info} isViewed={isViewed} onToggle={toggleViewed} />
                    ) : info.additions + info.deletions >= LARGE_FILE_THRESHOLD && !expandedLargeFiles.has(info.path) ? (
                      <LargeFilePlaceholder fileInfo={info} onShow={() => expandLargeFile(info.path)} onToggleViewed={toggleViewed} />
                    ) : (
                      <LazyDiffFileSection index={index} fileInfo={info} renderContent={() => (
                        <DiffFileSection
                          patch={patch}
                          fileInfo={info}
                          fileComments={commentsByFile.get(info.path) ?? EMPTY_COMMENTS}
                          activeInput={activeInput}
                          editingComment={editingComment}
                          toolbarState={toolbarState}
                          onRangeSelected={handleRangeSelected}
                          onStartEdit={handleStartEdit}
                          onAddComment={handleAddComment}
                          onUpdateComment={handleUpdateComment}
                          onDeleteComment={handleDeleteComment}
                          onCancelInput={handleCancelInput}
                          onToolbarComment={handleToolbarComment}
                          onToolbarSplit={selection?.type === 'branch' ? handleToolbarSplit : undefined}
                          fileSplitSelections={splitSelectionsByFile.get(info.path) ?? EMPTY_SPLIT_SELECTIONS}
                          onDeleteSplitSelection={selection?.type === 'branch' ? handleDeleteSplitSelection : undefined}
                          diffStyle={diffStyle}
                          resolvedTheme={resolvedTheme}
                          diffTheme={activeDiffTheme}
                          onToggleViewed={toggleViewed}
                        />
                      )} />
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </section>

      {selection?.type === 'branch' && splitLineSelections.length > 0 && !showSplitLinesDialog && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 px-4 py-2.5 bg-surface border border-green-500/30 rounded-lg shadow-lg">
          <span className="font-mono text-[0.75rem] text-text-primary">
            {splitLineSelections.length} line selection{splitLineSelections.length === 1 ? '' : 's'}
          </span>
          <button
            type="button"
            className="appearance-none border border-green-500/40 bg-green-500/10 text-green-400 text-[0.72rem] font-mono px-3 py-1 rounded-md cursor-pointer hover:bg-green-500/20 transition-colors duration-100"
            onClick={() => {
              setShowSplitLinesDialog(true);
              setSplitLinesError(null);
            }}
          >
            Split into new branch
          </button>
          <button
            type="button"
            className="appearance-none border border-border bg-transparent text-text-secondary text-[0.72rem] font-mono px-2.5 py-1 rounded-md cursor-pointer hover:bg-surface-hover hover:text-text-primary transition-colors duration-100"
            onClick={() => setSplitLineSelections([])}
          >
            Clear
          </button>
        </div>
      )}

      {showSplitLinesDialog && selection?.type === 'branch' && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 w-[400px]">
          <SplitLinesDialog
            selectionCount={splitLineSelections.length}
            onSubmit={handleSplitLinesSubmit}
            onCancel={handleSplitLinesCancel}
            isSplitting={isSplittingLines}
            splitError={splitLinesError}
          />
        </div>
      )}

      {isReviewDialogOpen && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 w-[320px]">
          <ReviewDialog
            models={reviewModels}
            onStart={handleStartReview}
            onClose={() => setIsReviewDialogOpen(false)}
            isReviewing={isReviewing}
          />
        </div>
      )}
    </main>
    </WorkerPoolContextProvider>
  );
}
