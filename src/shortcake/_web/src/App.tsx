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
import type { FileTreeRowDecoration, FileTreeSortComparator, GitStatusEntry } from '@pierre/trees';
import { prepareFileTreeInput } from '@pierre/trees';
import { FileTree as PierreFileTree, useFileTree } from '@pierre/trees/react';
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
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
  commitTime: number;
};

type WorkingStats = {
  files: number;
  additions: number;
  deletions: number;
};

type StackResponse = {
  currentBranch: string | null;
  branches: StackBranch[];
  workingStats?: WorkingStats | null;
};

type DiffResponse = {
  branch: string;
  parent: string;
  patch: string;
};

type WorkingDiffResponse = {
  patch: string;
};

type RecapSource = {
  kind: 'branch' | 'working';
  branch?: string;
  parent?: string;
  head: string;
  patchHash: string;
};

type RecapMeta = {
  id: string;
  title: string;
  createdAt: string;
  source: RecapSource;
  files: FileInfo[];
};

type RecapResponse = RecapMeta & {
  mdx: string;
  patch: string;
};

type RecapAnnotation = {
  line?: number;
  startLine?: number;
  endLine?: number;
  side?: AnnotationSide;
  text: string;
  title?: string;
  severity?: string;
  model?: string;
};

type UIStateResponse = StackResponse & {
  workingDiffKey: string;
};

type PersistedUIStateResponse = {
  version: number;
  diffStyle: DiffStyle;
  viewedFiles: Record<string, Record<string, string>>;
};

type PersistedUIStateUpdate = {
  diffStyle?: DiffStyle;
  viewedScope?: string;
  viewedFiles?: Record<string, string>;
};

type GitHubBranchInfo = {
  prNumber: number | null;
  prUrl: string | null;
  prIsDraft: boolean;
  prState?: 'open' | 'merged' | null;
  checkStatus: 'success' | 'failure' | 'pending' | null;
};

type GitHubInfoResponse = {
  branches: Record<string, GitHubBranchInfo>;
};

type DiffSelection =
  | { type: 'branch'; name: string }
  | { type: 'working' };

type RecapRoute = {
  id: string;
  sectionId: string | null;
};

function selectionFromHash(hash: string): DiffSelection | null {
  const path = hash.replace(/^#\/?/, '');
  if (path.startsWith('recap/')) return null;
  if (path === 'working') return { type: 'working' };
  if (path.startsWith('branch/')) {
    const name = decodeURIComponent(path.slice('branch/'.length));
    if (name) return { type: 'branch', name };
  }
  return null;
}

function recapRouteFromHash(hash: string): RecapRoute | null {
  const path = hash.replace(/^#\/?/, '');
  if (!path.startsWith('recap/')) return null;
  const rawRoute = path.slice('recap/'.length);
  const [idPart, query = ''] = rawRoute.split('?', 2);
  const id = decodeURIComponent(idPart ?? '');
  if (!id) return null;
  const sectionId = new URLSearchParams(query).get('section');
  return { id, sectionId: sectionId || null };
}

function recapIdFromHash(hash: string): string | null {
  return recapRouteFromHash(hash)?.id ?? null;
}

function recapRouteToHash(recapId: string, sectionId?: string): string {
  const route = `#/recap/${encodeURIComponent(recapId)}`;
  return sectionId ? `${route}?section=${encodeURIComponent(sectionId)}` : route;
}

function selectionToHash(sel: DiffSelection): string {
  if (sel.type === 'working') return '#/working';
  return `#/branch/${encodeURIComponent(sel.name)}`;
}

function diffSelectionsEqual(a: DiffSelection | null, b: DiffSelection | null): boolean {
  if (a === b) return true;
  if (!a || !b || a.type !== b.type) return false;
  if (a.type === 'working') return true;
  return b.type === 'branch' && a.name === b.name;
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
const DIFF_SIDEBAR_STORAGE_KEY = 'shortcake-diff-sidebar-width';
const DEFAULT_DIFF_SIDEBAR_WIDTH = 280;
const MIN_DIFF_SIDEBAR_WIDTH = 220;
const MAX_DIFF_SIDEBAR_WIDTH = 560;
const MIN_DIFF_PANE_WIDTH = 420;

function maxDiffSidebarWidth(): number {
  if (typeof window === 'undefined') return MAX_DIFF_SIDEBAR_WIDTH;
  return Math.max(
    MIN_DIFF_SIDEBAR_WIDTH,
    Math.min(MAX_DIFF_SIDEBAR_WIDTH, window.innerWidth - MIN_DIFF_PANE_WIDTH),
  );
}

function clampDiffSidebarWidth(width: number): number {
  return Math.min(maxDiffSidebarWidth(), Math.max(MIN_DIFF_SIDEBAR_WIDTH, width));
}

function loadDiffSidebarWidth(): number {
  if (typeof window === 'undefined') return DEFAULT_DIFF_SIDEBAR_WIDTH;
  const storedWidth = Number.parseFloat(localStorage.getItem(DIFF_SIDEBAR_STORAGE_KEY) ?? '');
  return clampDiffSidebarWidth(Number.isFinite(storedWidth) ? storedWidth : DEFAULT_DIFF_SIDEBAR_WIDTH);
}

function hashString(value: string): string {
  let h1 = 0xdeadbeef ^ value.length;
  let h2 = 0x41c6ce57 ^ value.length;

  for (let i = 0; i < value.length; i++) {
    const ch = value.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }

  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);

  return `${(h2 >>> 0).toString(36)}${(h1 >>> 0).toString(36)}`;
}

function viewedFilesScopeKey(selection: DiffSelection | null): string | null {
  if (!selection) return null;

  if (selection.type === 'working') return 'working';
  return `branch:${selection.name}`;
}

function loadPersistedViewedFileSet(
  persistedFiles: Record<string, string> | undefined,
  filePatchKeys: Map<string, string>,
): Set<string> {
  const viewed = new Set<string>();
  for (const [path, patchKey] of filePatchKeys) {
    if (persistedFiles?.[path] === patchKey) {
      viewed.add(path);
    }
  }
  return viewed;
}

function buildPersistedViewedFileRecord(
  viewedFiles: Set<string>,
  filePatchKeys: Map<string, string>,
): Record<string, string> {
  const files: Record<string, string> = {};

  for (const path of viewedFiles) {
    const patchKey = filePatchKeys.get(path);
    if (patchKey) files[path] = patchKey;
  }

  return files;
}

function viewedFileRecordsEqual(
  a: Record<string, string> | undefined,
  b: Record<string, string>,
): boolean {
  const aEntries = Object.entries(a ?? {});
  const bEntries = Object.entries(b);

  if (aEntries.length !== bEntries.length) return false;
  return bEntries.every(([path, patchKey]) => a?.[path] === patchKey);
}

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
    [data-annotation-content] {
      padding: 0;
    }
    [data-line-annotation] {
      --diffs-annotation-bg: ${resolvedTheme === 'light' ? '#fff7f9' : 'rgba(244, 63, 94, 0.07)'};
    }
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

async function postPersistedUIStateUpdate(
  update: PersistedUIStateUpdate,
): Promise<PersistedUIStateResponse | null> {
  const body = JSON.stringify(update);
  const path = '/api/review-state';

  if (typeof navigator !== 'undefined' && typeof navigator.sendBeacon === 'function') {
    const queued = navigator.sendBeacon(
      `${API_BASE}${path}`,
      new Blob([body], { type: 'application/json' }),
    );
    if (queued) return null;
  }

  return postJSON<PersistedUIStateResponse>(path, update);
}

function parsePatchStatus(patch: string): GitStatusEntry['status'] {
  if (/^new file mode /m.test(patch)) return 'added';
  if (/^deleted file mode /m.test(patch)) return 'deleted';
  if (/^rename from /m.test(patch)) return 'renamed';
  return 'modified';
}

function patchFilePath(patch: string, index: number): string {
  return patch.match(/^diff --git a\/.+ b\/(.+)$/m)?.[1] ?? `file-${index}`;
}

function parseFileInfo(patch: string, index: number): FileInfo {
  const path = patchFilePath(patch, index);
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

// Order the per-file patches to match the sidebar file tree (folders first,
// natural sort) so the diff list and the tree stay in sync. We reuse the tree
// library's own prepare step so the ordering is guaranteed identical.
function orderPatchesForTree(patches: string[]): string[] {
  if (patches.length <= 1) return patches;
  const treeRank = new Map(
    prepareFileTreeInput(patches.map(patchFilePath)).paths.map((path, rank) => [path, rank]),
  );
  return patches
    .map((patch, index) => ({ patch, index, rank: treeRank.get(patchFilePath(patch, index)) ?? index }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((entry) => entry.patch);
}

function buildChangedFilesTreeUnsafeCSS(): string {
  return `
    :host {
      container-type: inline-size;
    }
    [data-type='item'] {
      min-width: 0;
      overflow: hidden;
      transition: color 100ms ease-in-out, background-color 100ms ease-in-out;
    }
    [data-type='item']:hover {
      color: var(--trees-selected-fg);
      background: var(--trees-bg-muted);
    }
    [data-truncate-marker] {
      background-color: var(--color-surface);
      background-image: none;
    }
    [data-type='item']:hover [data-truncate-marker] {
      background-color: var(--color-surface-hover);
    }
    [data-type='item'][data-item-selected='true'] [data-truncate-marker] {
      background-color: color-mix(in srgb, var(--color-accent) 8%, var(--color-surface));
    }
    [data-item-section='content'] {
      flex: 1 1 auto;
      min-width: 0;
    }
    [data-item-section='decoration'] {
      flex: none;
      max-width: 88px;
      margin-left: 6px;
      font-family: var(--trees-font-family);
      font-size: 0.6rem;
      font-weight: 500;
      letter-spacing: 0;
      color: var(--trees-fg-muted);
    }
    @container (width < 260px) {
      [data-item-section='decoration'] {
        display: none;
      }
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
  preserveInputOrder?: boolean;
  search?: boolean;
  onFilterChange: (value: string) => void;
  onFileClick: (index: number) => void;
};

function ChangedFilesTree({
  fileInfos,
  fileFilter,
  activeFileIndex,
  viewedFiles,
  resolvedTheme,
  preserveInputOrder = false,
  search = true,
  onFilterChange,
  onFileClick,
}: ChangedFilesTreeProps) {
  const paths = useMemo(() => fileInfos.map((file) => file.path), [fileInfos]);
  const fileOrder = useMemo(
    () => new Map(paths.map((path, index) => [path, index])),
    [paths],
  );
  const fileOrderRef = useRef(fileOrder);
  const isSyncingSelectionRef = useRef(false);
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

  fileOrderRef.current = fileOrder;

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

  const inputOrderSort = useCallback<FileTreeSortComparator>((left, right) => {
    const order = fileOrderRef.current;
    const leftRank = order.get(left.path);
    const rightRank = order.get(right.path);

    if (leftRank !== undefined && rightRank !== undefined) return leftRank - rightRank;
    if (leftRank !== undefined) return -1;
    if (rightRank !== undefined) return 1;
    return left.path.localeCompare(right.path);
  }, []);

  const handleSelectionChange = useCallback((selectedPaths: readonly string[]) => {
    if (isSyncingSelectionRef.current) return;
    const selectedPath = [...selectedPaths]
      .reverse()
      .find((path) => fileByPathRef.current.has(path));
    if (!selectedPath) return;

    const selectedIndex = fileInfos.findIndex((file) => file.path === selectedPath);
    if (selectedIndex >= 0) onFileClick(selectedIndex);
  }, [fileInfos, onFileClick]);

  const handleTreeClick = useCallback((event: React.MouseEvent<HTMLDivElement>) => {
    const clickedItem = event.nativeEvent.composedPath().find(
      (target): target is HTMLElement =>
        target instanceof HTMLElement && target.dataset.itemType === 'file',
    );
    const path = clickedItem?.dataset.itemPath;
    if (!path) return;
    const selectedIndex = fileInfos.findIndex((file) => file.path === path);
    if (selectedIndex >= 0) onFileClick(selectedIndex);
  }, [fileInfos, onFileClick]);

  const { model } = useFileTree({
    density: 'compact',
    fileTreeSearchMode: 'hide-non-matches',
    flattenEmptyDirectories: false,
    gitStatus,
    initialExpansion: 'open',
    initialSearchQuery: search && fileFilter ? fileFilter : null,
    initialSelectedPaths: activePath ? [activePath] : [],
    itemHeight: FILE_TREE_ITEM_HEIGHT,
    onSearchChange: search ? (value) => onFilterChange(value ?? '') : undefined,
    onSelectionChange: handleSelectionChange,
    overscan: 16,
    paths,
    renderRowDecoration,
    search,
    searchBlurBehavior: 'retain',
    stickyFolders: false,
    sort: preserveInputOrder ? inputOrderSort : undefined,
    unsafeCSS: buildChangedFilesTreeUnsafeCSS(),
  });

  useEffect(() => {
    model.resetPaths(paths);
  }, [model, paths]);

  useEffect(() => {
    if (search) model.setSearch(fileFilter.trim() ? fileFilter : null);
  }, [fileFilter, model, search]);

  useEffect(() => {
    model.setGitStatus(gitStatus);
  }, [gitStatus, model, viewedFiles]);

  useEffect(() => {
    const selectedPaths = model.getSelectedPaths();
    let frame: number | null = null;

    if (activePath) {
      if (selectedPaths.length !== 1 || selectedPaths[0] !== activePath) {
        isSyncingSelectionRef.current = true;
        for (const path of selectedPaths) {
          if (path !== activePath) model.getItem(path)?.deselect();
        }
        model.getItem(activePath)?.select();
        frame = window.requestAnimationFrame(() => {
          isSyncingSelectionRef.current = false;
        });
      }
      model.scrollToPath(activePath, { focus: false, offset: 'nearest' });
      return () => {
        if (frame !== null) window.cancelAnimationFrame(frame);
        isSyncingSelectionRef.current = false;
      };
    }

    for (const path of selectedPaths) {
      model.getItem(path)?.deselect();
    }
    return undefined;
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
      '--trees-item-row-gap-override': '3px',
      '--trees-level-gap-override': '10px',
      '--trees-padding-inline-override': '0px',
      '--trees-search-bg-override': 'var(--color-surface-hover)',
      '--trees-search-fg-override': 'var(--color-text-primary)',
      '--trees-search-font-weight-override': '400',
      '--trees-selected-bg-override': 'color-mix(in srgb, var(--color-accent) 8%, var(--color-surface))',
      '--trees-selected-fg-override': 'var(--color-text-primary)',
      '--trees-status-added-override': 'var(--color-stat-add)',
      '--trees-status-deleted-override': 'var(--color-stat-del)',
      '--trees-status-modified-override': 'var(--color-accent)',
      '--trees-status-renamed-override': resolvedTheme === 'light' ? '#a16207' : '#facc15',
    }) as React.CSSProperties,
    [resolvedTheme],
  );

  return (
    <div
      className="h-full flex-1 min-h-0 overflow-hidden px-2.5 pt-2 pb-1.5"
      onClick={handleTreeClick}
    >
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

function FileCollapseToggle({
  isCollapsed,
  onToggle,
}: {
  isCollapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className="relative inline-flex size-6 shrink-0 appearance-none items-center justify-center rounded border-0 bg-transparent p-0 text-text-muted hover:bg-surface-active hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      aria-label={isCollapsed ? 'Expand file' : 'Collapse file'}
      aria-expanded={!isCollapsed}
      onClick={(event) => {
        event.stopPropagation();
        onToggle();
      }}
    >
      <svg
        className={`size-3.5 transition-transform duration-150 ${isCollapsed ? '-rotate-90' : ''}`}
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="m4 6 4 4 4-4" />
      </svg>
      <span
        className="pointer-fine:hidden absolute top-1/2 left-1/2 size-[max(100%,3rem)] -translate-1/2"
        aria-hidden="true"
      />
    </button>
  );
}

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
      className={`relative flex items-center gap-1.5 appearance-none border-0 bg-transparent px-1 py-0.5 rounded cursor-pointer select-none font-mono text-[0.7rem] ${
        isViewed ? 'text-accent' : 'text-text-secondary hover:text-text-primary'
      }`}
      aria-pressed={isViewed}
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
      <span
        className="pointer-fine:hidden absolute top-1/2 left-1/2 size-[max(100%,3rem)] -translate-1/2"
        aria-hidden="true"
      />
    </button>
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
      <div className="flex flex-col items-center justify-center py-8 gap-2 mx-3 my-3 rounded-md border border-warning/30 bg-warning-bg">
        <p className="text-[0.8rem] text-warning">
          Large file — <span className="font-mono text-[0.72rem]">{fileInfo.additions + fileInfo.deletions}</span> lines changed
        </p>
        <button
          type="button"
          className="appearance-none border border-warning/40 bg-warning/10 text-warning text-[0.75rem] font-mono px-3 py-1 rounded cursor-pointer hover:bg-warning/15 hover:border-warning/60 transition-colors duration-100"
          onClick={onShow}
        >
          Show changes
        </button>
      </div>
    </div>
  );
}

const EMPTY_COMMENTS: DiffComment[] = [];

const DiffFileSection = React.memo(function DiffFileSection({
  patch,
  fileInfo,
  fileComments,
  activeInput,
  editingComment,
  onRangeSelected,
  onStartEdit,
  onAddComment,
  onUpdateComment,
  onDeleteComment,
  onCancelInput,
  diffStyle,
  resolvedTheme,
  diffTheme,
  isCollapsed,
  isViewed,
  onToggleCollapsed,
  onToggleViewed,
}: {
  patch: string;
  fileInfo: FileInfo;
  fileComments: DiffComment[];
  activeInput: ActiveInput;
  editingComment: DiffComment | null;
  onRangeSelected: (file: string, startLine: number, endLine: number, side: AnnotationSide) => void;
  onStartEdit: (comment: DiffComment) => void;
  onAddComment: (file: string, startLine: number, endLine: number, side: AnnotationSide, text: string) => void;
  onUpdateComment: (id: string, text: string) => void;
  onDeleteComment: (id: string) => void;
  onCancelInput: () => void;
  diffStyle: DiffStyle;
  resolvedTheme?: 'dark' | 'light';
  diffTheme?: string;
  isCollapsed?: boolean;
  isViewed?: boolean;
  onToggleCollapsed?: (path: string) => void;
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
  const fileLanguage = getFiletypeFromFileName(fileInfo.path);

  const fileDiff = useMemo(
    () => setLanguageOverride(getSingularPatch(patch), fileLanguage),
    [patch, fileLanguage],
  );
  const shouldPreloadHighlightedDiff = fileLanguage !== 'text' && !isCollapsed;

  const options = useMemo<FileDiffProps<CommentMeta>['options']>(
    () => ({
      diffStyle,
      diffIndicators: 'classic',
      hunkSeparators: 'metadata',
      theme: activeTheme,
      themeType: rt,
      collapsed: isCollapsed,
      overflow: 'scroll',
      lineDiffType: 'word',
      enableLineSelection: true,
      onLineSelectionEnd: handleSelectionEnd,
      unsafeCSS: buildDiffUnsafeCSS(rt),
    }),
    [diffStyle, handleSelectionEnd, rt, activeTheme, isCollapsed],
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
    if (activeInput && activeInput.file === fileInfo.path) {
      return { start: activeInput.startLine, end: activeInput.endLine, side: activeInput.side };
    }
    if (editingComment && editingComment.file === fileInfo.path) {
      return { start: editingComment.startLine, end: editingComment.endLine, side: editingComment.side };
    }
    return null;
  }, [activeInput, editingComment, fileInfo.path]);

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

    return annotations;
  }, [fileComments, activeInput, editingComment, fileInfo.path]);

  const renderAnnotation = useCallback(
    (annotation: DiffLineAnnotation<CommentMeta>) => {
      const { metadata } = annotation;

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
    [fileComments, editingComment, activeInput, fileInfo.path, onAddComment, onUpdateComment, onDeleteComment, onCancelInput, onStartEdit],
  );

  const renderHeaderMetadata = useCallback(() => {
    if (!onToggleViewed) return null;
    return <ViewedToggle isViewed={isViewed ?? false} onToggle={() => onToggleViewed(fileInfo.path)} />;
  }, [fileInfo.path, isViewed, onToggleViewed]);

  const renderHeaderPrefix = useCallback(() => {
    if (!onToggleCollapsed) return null;
    return (
      <FileCollapseToggle
        isCollapsed={isCollapsed ?? false}
        onToggle={() => onToggleCollapsed(fileInfo.path)}
      />
    );
  }, [fileInfo.path, isCollapsed, onToggleCollapsed]);

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
      renderHeaderPrefix={onToggleCollapsed ? renderHeaderPrefix : undefined}
      renderHeaderMetadata={onToggleViewed ? renderHeaderMetadata : undefined}
      prerenderedHTML={prerenderedDiffHTML}
      disableWorkerPool={shouldPreloadHighlightedDiff}
    />
  );
});

type RecapComponentName =
  | 'FileMap'
  | 'Diff'
  | 'DiffTabs'
  | 'Mermaid'
  | 'DataModel'
  | 'Endpoint'
  | 'StateSummary';

type RecapBlock =
  | { type: 'heading'; depth: number; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'list'; ordered: boolean; items: string[] }
  | { type: 'code'; language: string; code: string }
  | { type: 'table'; headers: string[]; rows: string[][] }
  | { type: 'component'; name: RecapComponentName; props: Record<string, string>; children?: string };

type RecapDiffTabFile = {
  path: string;
  summary?: string;
  annotations?: RecapAnnotation[];
};

type RecapRenderContext = {
  fileInfos: FileInfo[];
  patchByPath: Map<string, string>;
  resolvedTheme: 'dark' | 'light';
  diffTheme: string;
  diffStyle: DiffStyle;
};

type RecapAnnotationMeta = {
  text: string;
  title?: string;
  lineLabel: string;
};

const SUPPORTED_RECAP_COMPONENTS = new Set<RecapComponentName>([
  'FileMap',
  'Diff',
  'DiffTabs',
  'Mermaid',
  'DataModel',
  'Endpoint',
  'StateSummary',
]);

const EMPTY_VIEWED_FILES = new Set<string>();
const FILE_TREE_ITEM_HEIGHT = 26;
const RECAP_FILE_SELECT_EVENT = 'shortcake:recap-file-select';
let recapMermaidRenderSerial = 0;

function recapDiffElementId(path: string): string {
  return `recap-diff-${hashString(path)}`;
}

function selectedRecapFilePath(event: Event): string | null {
  const detail = (event as CustomEvent<{ path?: unknown }>).detail;
  return typeof detail?.path === 'string' ? detail.path : null;
}

function scrollToRecapDiff(path: string): void {
  const scroll = () => {
    document.getElementById(recapDiffElementId(path))?.scrollIntoView({
      block: 'start',
      behavior: 'smooth',
    });
  };
  window.requestAnimationFrame(() => window.requestAnimationFrame(scroll));
}

function recapHeadingId(block: RecapBlock, index: number): string {
  if (block.type !== 'heading') return `recap-block-${index}`;
  const slug = block.text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 48);
  return `recap-${slug || 'section'}-${hashString(`${index}:${block.text}`)}`;
}

function isFileMapBlock(block: RecapBlock): boolean {
  return block.type === 'component' && block.name === 'FileMap';
}

function stripRecapFrontmatter(mdx: string): string {
  return mdx.replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, '');
}

function findUnquotedRecapTagEnd(value: string, start = 0): number | null {
  let quote: string | null = null;
  for (let index = start; index < value.length; index += 1) {
    const char = value[index];
    if (quote) {
      if (char === quote) quote = null;
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
    } else if (char === '>') {
      return index;
    }
  }
  return null;
}

function collapseRecapComponentTagNewlines(body: string): string {
  const lines = body.split(/\r?\n/);
  const output: string[] = [];
  let pending: string[] | null = null;
  let inFence = false;

  for (const line of lines) {
    if (!pending && line.trimStart().startsWith('```')) {
      inFence = !inFence;
      output.push(line);
      continue;
    }

    if (inFence) {
      output.push(line);
      continue;
    }

    if (pending) {
      pending.push(line);
      const joined = pending.join('\n');
      if (findUnquotedRecapTagEnd(joined) !== null) {
        output.push(joined.replace(/\s*\r?\n\s*/g, ' '));
        pending = null;
      }
      continue;
    }

    const trimmed = line.trimStart();
    const componentStart = trimmed.match(/^<\s*[A-Z][A-Za-z0-9]*\b/);
    if (componentStart && findUnquotedRecapTagEnd(trimmed, componentStart[0].length) === null) {
      pending = [line];
      continue;
    }

    output.push(line);
  }

  if (pending) {
    output.push(...pending);
  }

  return output.join('\n');
}

function parseRecapAttributes(raw: string, name: string): Record<string, string> {
  if (/\bon[A-Z][A-Za-z0-9_]*\s*=/.test(raw)) {
    throw new Error(`<${name}> uses an event handler prop`);
  }
  if (/=\s*{/.test(raw)) {
    throw new Error(`<${name}> uses a JS expression prop`);
  }

  const attrs: Record<string, string> = {};
  let remainder = raw;
  const attrRe = /([A-Za-z_:][\w:.-]*)\s*=\s*("[^"]*"|'[^']*')/g;
  let match: RegExpExecArray | null;
  while ((match = attrRe.exec(raw)) !== null) {
    const attrName = match[1]!;
    const quoted = match[2]!;
    attrs[attrName] = quoted.slice(1, -1);
    remainder = remainder.replace(match[0], ' '.repeat(match[0].length));
  }

  if (remainder.trim().replace(/\/$/, '').trim() !== '') {
    throw new Error(`<${name}> has a non-static prop`);
  }

  return attrs;
}

function validateRecapLine(line: string, lineNumber: number): void {
  if (/^\s*(import|export)\b/.test(line)) {
    throw new Error(`MDX import/export is not supported on line ${lineNumber}`);
  }
  if (/^\s*{.*}\s*$/.test(line)) {
    throw new Error(`MDX expressions are not supported on line ${lineNumber}`);
  }

  const componentRe = /<\s*\/?\s*([A-Z][A-Za-z0-9]*)\b([^>]*)>/g;
  let match: RegExpExecArray | null;
  while ((match = componentRe.exec(line)) !== null) {
    const name = match[1] as RecapComponentName;
    if (!SUPPORTED_RECAP_COMPONENTS.has(name)) {
      throw new Error(`Unsupported MDX component <${match[1]}> on line ${lineNumber}`);
    }
    parseRecapAttributes(match[2] ?? '', match[1]!);
  }
}

function isRecapTableStart(lines: string[], index: number): boolean {
  const current = lines[index] ?? '';
  const next = lines[index + 1] ?? '';
  return current.includes('|') && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(next);
}

function splitRecapTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}

function parseRecapComponentLine(
  line: string,
): { name: RecapComponentName; props: Record<string, string>; selfClosing: boolean } | null {
  const trimmed = line.trim();
  const selfClosing = trimmed.match(/^<([A-Z][A-Za-z0-9]*)([\s\S]*?)\/>$/);
  if (selfClosing) {
    const name = selfClosing[1] as RecapComponentName;
    if (!SUPPORTED_RECAP_COMPONENTS.has(name)) {
      throw new Error(`Unsupported MDX component <${selfClosing[1]}>`);
    }
    return {
      name,
      props: parseRecapAttributes(selfClosing[2] ?? '', name),
      selfClosing: true,
    };
  }

  const blockStart = trimmed.match(/^<([A-Z][A-Za-z0-9]*)([\s\S]*?)>$/);
  if (!blockStart || trimmed.startsWith('</')) return null;

  const name = blockStart[1] as RecapComponentName;
  if (!SUPPORTED_RECAP_COMPONENTS.has(name)) {
    throw new Error(`Unsupported MDX component <${blockStart[1]}>`);
  }
  return {
    name,
    props: parseRecapAttributes(blockStart[2] ?? '', name),
    selfClosing: false,
  };
}

function recapListItemMatch(line: string, ordered: boolean): RegExpMatchArray | null {
  return ordered
    ? line.match(/^\s*\d+[.)]\s+(.+)$/)
    : line.match(/^\s*[-*]\s+(.+)$/);
}

function isRecapAnyListItem(line: string): boolean {
  return /^\s*[-*]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line);
}

function isRecapBlockBoundary(lines: string[], index: number): boolean {
  const line = lines[index] ?? '';
  const trimmed = line.trim();
  return (
    trimmed === '' ||
    trimmed.startsWith('```') ||
    /^#{1,6}\s+/.test(line) ||
    isRecapAnyListItem(line) ||
    parseRecapComponentLine(line) !== null ||
    isRecapTableStart(lines, index)
  );
}

function parseRestrictedRecapMdx(mdx: string): RecapBlock[] {
  const body = collapseRecapComponentTagNewlines(stripRecapFrontmatter(mdx));
  const lines = body.split(/\r?\n/);
  const blocks: RecapBlock[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i] ?? '';
    const trimmed = line.trim();
    if (trimmed === '') {
      i += 1;
      continue;
    }

    if (trimmed.startsWith('```')) {
      const language = trimmed.slice(3).trim();
      const codeLines: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] ?? '').trim().startsWith('```')) {
        codeLines.push(lines[i] ?? '');
        i += 1;
      }
      if (i < lines.length) i += 1;
      blocks.push({ type: 'code', language, code: codeLines.join('\n') });
      continue;
    }

    validateRecapLine(line, i + 1);
    const component = parseRecapComponentLine(line);
    if (component) {
      if (component.selfClosing) {
        blocks.push({ type: 'component', name: component.name, props: component.props });
        i += 1;
        continue;
      }

      const children: string[] = [];
      const closeTag = `</${component.name}>`;
      i += 1;
      while (i < lines.length && (lines[i] ?? '').trim() !== closeTag) {
        children.push(lines[i] ?? '');
        i += 1;
      }
      if (i >= lines.length) {
        throw new Error(`<${component.name}> is missing a closing tag`);
      }
      blocks.push({
        type: 'component',
        name: component.name,
        props: component.props,
        children: children.join('\n'),
      });
      i += 1;
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      blocks.push({
        type: 'heading',
        depth: heading[1]!.length,
        text: heading[2]!.trim(),
      });
      i += 1;
      continue;
    }

    if (isRecapAnyListItem(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items: string[] = [];
      while (i < lines.length) {
        const itemLine = lines[i] ?? '';
        const itemMatch = recapListItemMatch(itemLine, ordered);
        if (!itemMatch) break;
        validateRecapLine(itemLine, i + 1);
        const itemParts = [itemMatch[1]!.trim()];
        i += 1;

        while (i < lines.length && !isRecapBlockBoundary(lines, i)) {
          const continuationLine = lines[i] ?? '';
          validateRecapLine(continuationLine, i + 1);
          itemParts.push(continuationLine.trim());
          i += 1;
        }

        items.push(itemParts.join(' '));
      }
      blocks.push({ type: 'list', ordered, items });
      continue;
    }

    if (isRecapTableStart(lines, i)) {
      const headers = splitRecapTableRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && (lines[i] ?? '').includes('|') && (lines[i] ?? '').trim() !== '') {
        validateRecapLine(lines[i] ?? '', i + 1);
        rows.push(splitRecapTableRow(lines[i] ?? ''));
        i += 1;
      }
      blocks.push({ type: 'table', headers, rows });
      continue;
    }

    const paragraph: string[] = [];
    while (i < lines.length) {
      const paragraphLine = lines[i] ?? '';
      const paragraphTrimmed = paragraphLine.trim();
      if (
        isRecapBlockBoundary(lines, i)
      ) {
        break;
      }
      validateRecapLine(paragraphLine, i + 1);
      paragraph.push(paragraphTrimmed);
      i += 1;
    }
    blocks.push({ type: 'paragraph', text: paragraph.join(' ') });
  }

  return blocks;
}

function renderRecapInline(text: string): React.ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((part, index) => {
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code key={index} className="rounded bg-surface-hover px-1 py-0.5 font-mono text-[0.78em] text-text-primary">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    return part;
  });
}

function parseRecapJSON<T>(raw: string | undefined, fallback: T): T {
  if (!raw?.trim()) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function extractFencedPayload(children: string | undefined): string {
  if (!children) return '';
  const match = children.match(/```(?:json)?\s*([\s\S]*?)```/i);
  return (match?.[1] ?? children).trim();
}

function normalizeRecapAnnotations(value: unknown): RecapAnnotation[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item): RecapAnnotation[] => {
    if (!item || typeof item !== 'object') return [];
    const raw = item as Record<string, unknown>;
    if (typeof raw.text !== 'string') return [];
    const line = typeof raw.line === 'number' ? raw.line : undefined;
    const startLine = typeof raw.startLine === 'number' ? raw.startLine : line;
    const endLine = typeof raw.endLine === 'number' ? raw.endLine : startLine;
    return [{
      line,
      startLine,
      endLine,
      side: normalizeAnnotationSide(raw.side),
      text: raw.text,
      title: typeof raw.title === 'string' ? raw.title : undefined,
      severity: typeof raw.severity === 'string' ? raw.severity : undefined,
      model: typeof raw.model === 'string' ? raw.model : undefined,
    }];
  });
}

function formatRecapAnnotationLineLabel(annotation: RecapAnnotation): string {
  const startLine = annotation.startLine ?? annotation.line ?? annotation.endLine ?? 1;
  const endLine = annotation.endLine ?? annotation.line ?? startLine;
  return formatLineLabel(startLine, endLine);
}

function normalizeRecapDiffTabs(value: unknown): RecapDiffTabFile[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item): RecapDiffTabFile[] => {
    if (typeof item === 'string') return [{ path: item }];
    if (!item || typeof item !== 'object') return [];
    const raw = item as Record<string, unknown>;
    if (typeof raw.path !== 'string') return [];
    return [{
      path: raw.path,
      summary: typeof raw.summary === 'string' ? raw.summary : undefined,
      annotations: normalizeRecapAnnotations(raw.annotations),
    }];
  });
}

function recapDocumentDiffPaths(blocks: { block: RecapBlock }[]): string[] {
  const paths: string[] = [];
  const seen = new Set<string>();
  const addPath = (path: string | undefined) => {
    if (!path || seen.has(path)) return;
    seen.add(path);
    paths.push(path);
  };

  for (const { block } of blocks) {
    if (block.type !== 'component') continue;
    if (block.name === 'Diff') {
      addPath(block.props.path);
    } else if (block.name === 'DiffTabs') {
      for (const file of normalizeRecapDiffTabs(parseRecapJSON(block.props.files, []))) {
        addPath(file.path);
      }
    }
  }

  return paths;
}

function orderRecapFileInfosForDocument(
  fileInfos: FileInfo[],
  documentPaths: string[],
): FileInfo[] {
  if (documentPaths.length === 0) return fileInfos;

  const documentRank = new Map(documentPaths.map((path, index) => [path, index]));
  return fileInfos
    .map((file, index) => ({ file, index, rank: documentRank.get(file.path) }))
    .sort((a, b) => {
      if (a.rank !== undefined && b.rank !== undefined) return a.rank - b.rank;
      if (a.rank !== undefined) return -1;
      if (b.rank !== undefined) return 1;
      return a.index - b.index;
    })
    .map((entry) => entry.file);
}

function recapDisplayTitle(recap: RecapResponse): string {
  const rangeMatch = recap.title.match(/^Recap:\s+.+\.\.\.(.+)$/);
  if (rangeMatch?.[1]) return rangeMatch[1];

  const recapPrefixMatch = recap.title.match(/^Recap:\s+(.+)$/);
  if (recapPrefixMatch?.[1]) {
    const title = recapPrefixMatch[1];
    return title === 'working changes' ? 'Working changes' : title;
  }

  return recap.title;
}

function recapFileTreeRowCount(fileInfos: FileInfo[]): number {
  const directories = new Set<string>();

  for (const file of fileInfos) {
    const segments = file.path.split('/').filter(Boolean);
    for (let index = 1; index < segments.length; index += 1) {
      directories.add(segments.slice(0, index).join('/'));
    }
  }

  return fileInfos.length + directories.size;
}

function recapFileTreeHeight(fileInfos: FileInfo[]): React.CSSProperties {
  const rowCount = recapFileTreeRowCount(fileInfos);
  return {
    height: `${Math.max(56, rowCount * FILE_TREE_ITEM_HEIGHT + 18)}px`,
  };
}

function normalizeAnnotationSide(side: unknown): AnnotationSide {
  return side === 'deletions' || side === 'left' ? 'deletions' : 'additions';
}

function RecapFileMap({
  fileInfos,
  documentPaths = [],
  resolvedTheme,
  className = '',
}: {
  fileInfos: FileInfo[];
  documentPaths?: string[];
  resolvedTheme: 'dark' | 'light';
  className?: string;
}) {
  const [fileFilter, setFileFilter] = useState('');
  const [activeFileIndex, setActiveFileIndex] = useState<number | null>(null);
  const documentPathSet = useMemo(
    () => new Set(documentPaths),
    [documentPaths],
  );
  const documentFileInfos = useMemo(
    () => documentPaths.length === 0
      ? fileInfos
      : fileInfos.filter((file) => documentPathSet.has(file.path)),
    [documentPathSet, documentPaths.length, fileInfos],
  );
  const remainingFileInfos = useMemo(
    () => documentPaths.length === 0
      ? []
      : fileInfos.filter((file) => !documentPathSet.has(file.path)),
    [documentPathSet, documentPaths.length, fileInfos],
  );
  const activePath = activeFileIndex == null ? null : fileInfos[activeFileIndex]?.path ?? null;
  const activeDocumentFileIndex = activePath == null
    ? null
    : documentFileInfos.findIndex((file) => file.path === activePath);
  const activeRemainingFileIndex = activePath == null
    ? null
    : remainingFileInfos.findIndex((file) => file.path === activePath);
  const indexByPath = useMemo(
    () => new Map(fileInfos.map((file, index) => [file.path, index])),
    [fileInfos],
  );
  const totals = useMemo(
    () => fileInfos.reduce(
      (acc, file) => ({
        additions: acc.additions + file.additions,
        deletions: acc.deletions + file.deletions,
      }),
      { additions: 0, deletions: 0 },
    ),
    [fileInfos],
  );

  const handleFileClick = useCallback((index: number) => {
    setActiveFileIndex(index);
    const path = fileInfos[index]?.path;
    if (!path) return;
    window.dispatchEvent(new CustomEvent(RECAP_FILE_SELECT_EVENT, { detail: { path } }));
    scrollToRecapDiff(path);
  }, [fileInfos]);

  const handleDocumentFileClick = useCallback((index: number) => {
    const path = documentFileInfos[index]?.path;
    const globalIndex = path ? indexByPath.get(path) : undefined;
    if (globalIndex !== undefined) handleFileClick(globalIndex);
  }, [documentFileInfos, handleFileClick, indexByPath]);

  const handleRemainingFileClick = useCallback((index: number) => {
    const path = remainingFileInfos[index]?.path;
    const globalIndex = path ? indexByPath.get(path) : undefined;
    if (globalIndex !== undefined) handleFileClick(globalIndex);
  }, [handleFileClick, indexByPath, remainingFileInfos]);

  return (
    <section className={className}>
      <div className="mb-4 flex items-end justify-between gap-3">
        <div>
          <h2 className="font-mono text-[0.68rem] font-semibold uppercase tracking-normal text-text-muted">
            Files changed
          </h2>
          <p className="m-0 mt-3 font-mono text-[0.7rem] text-text-secondary">
            {fileInfos.length} file{fileInfos.length === 1 ? '' : 's'}
          </p>
        </div>
        <div className="flex shrink-0 gap-1.5 font-mono text-[0.66rem]">
          {totals.additions > 0 && (
            <span className="text-stat-add">+{totals.additions}</span>
          )}
          {totals.deletions > 0 && (
            <span className="text-blue-400">-{totals.deletions}</span>
          )}
        </div>
      </div>
      <div className="pr-1" data-recap-file-map-list>
        <div style={recapFileTreeHeight(documentFileInfos)}>
          <ChangedFilesTree
            fileInfos={documentFileInfos}
            fileFilter={fileFilter}
            activeFileIndex={activeDocumentFileIndex === -1 ? null : activeDocumentFileIndex}
            viewedFiles={EMPTY_VIEWED_FILES}
            resolvedTheme={resolvedTheme}
            preserveInputOrder
            search={false}
            onFilterChange={setFileFilter}
            onFileClick={handleDocumentFileClick}
          />
        </div>
        {remainingFileInfos.length > 0 && (
          <>
            <p className="m-0 mt-4 border-t border-border pt-4 font-mono text-[0.62rem] font-semibold uppercase tracking-normal text-text-muted">
              Other files
            </p>
            <div className="mt-2" style={recapFileTreeHeight(remainingFileInfos)}>
              <ChangedFilesTree
                fileInfos={remainingFileInfos}
                fileFilter={fileFilter}
                activeFileIndex={activeRemainingFileIndex === -1 ? null : activeRemainingFileIndex}
                viewedFiles={EMPTY_VIEWED_FILES}
                resolvedTheme={resolvedTheme}
                preserveInputOrder
                search={false}
                onFilterChange={setFileFilter}
                onFileClick={handleRemainingFileClick}
              />
            </div>
          </>
        )}
      </div>
    </section>
  );
}

function RecapDiffBlock({
  path,
  summary,
  annotations = [],
  context,
}: {
  path: string;
  summary?: string;
  annotations?: RecapAnnotation[];
  context: RecapRenderContext;
}) {
  const patch = context.patchByPath.get(path);
  const fileLanguage = getFiletypeFromFileName(path || 'text.txt');
  const fileDiff = useMemo(
    () => (patch ? setLanguageOverride(getSingularPatch(patch), fileLanguage) : null),
    [patch, fileLanguage],
  );
  const options = useMemo<FileDiffProps<RecapAnnotationMeta>['options']>(
    () => ({
      diffStyle: context.diffStyle,
      diffIndicators: 'classic',
      hunkSeparators: 'metadata',
      theme: context.diffTheme,
      themeType: context.resolvedTheme,
      overflow: 'scroll',
      lineDiffType: 'word',
      enableLineSelection: false,
      unsafeCSS: buildDiffUnsafeCSS(context.resolvedTheme),
    }),
    [context.diffStyle, context.diffTheme, context.resolvedTheme],
  );
  const lineAnnotations = useMemo<DiffLineAnnotation<RecapAnnotationMeta>[]>(
    () => annotations.map((annotation) => ({
      lineNumber: annotation.endLine ?? annotation.line ?? annotation.startLine ?? 1,
      side: normalizeAnnotationSide(annotation.side),
      metadata: {
        text: annotation.text,
        title: annotation.title,
        lineLabel: formatRecapAnnotationLineLabel(annotation),
      },
    })),
    [annotations],
  );
  const renderAnnotation = useCallback(
    (annotation: DiffLineAnnotation<RecapAnnotationMeta>) => {
      return (
        <aside
          className="w-full border-y border-recap-comment-border bg-recap-comment-bg px-3 py-2.5 font-sans"
          aria-label={`Review comment for ${annotation.metadata.lineLabel}`}
          data-recap-inline-comment
          data-recap-inline-comment-line={annotation.metadata.lineLabel}
        >
          <div className="flex min-w-0 items-start gap-3">
            <div className="inline-flex size-6 shrink-0 items-center justify-center rounded-[6px] border border-recap-comment-chip-border bg-recap-comment-chip-bg">
              <img
                src="/favicon.svg"
                alt=""
                className="size-3.5"
                aria-hidden="true"
              />
            </div>
            <div className="min-w-0 flex-1">
              <p className="m-0 whitespace-pre-wrap break-words text-[0.86rem] leading-6 text-text-secondary">
                {annotation.metadata.title && (
                  <strong className="font-semibold text-text-primary">{annotation.metadata.title}. </strong>
                )}
                {annotation.metadata.text}
              </p>
            </div>
          </div>
        </aside>
      );
    },
    [],
  );

  if (!patch || !fileDiff) {
    return (
      <section className="my-5 rounded-md border border-danger/30 bg-danger/5 px-4 py-3 text-[0.82rem] text-danger">
        Diff path not found: <code className="font-mono">{path}</code>
      </section>
    );
  }

  return (
    <section
      id={recapDiffElementId(path)}
      data-recap-diff-path={path}
      className="my-5 overflow-hidden rounded-md border border-border bg-surface"
    >
      {summary && (
        <p className="m-0 border-b border-border bg-surface-hover px-4 py-3 text-[0.78rem] leading-relaxed text-text-secondary">
          {summary}
        </p>
      )}
      <div className="diff-content">
        <FileDiff<RecapAnnotationMeta>
          fileDiff={fileDiff}
          options={options}
          lineAnnotations={lineAnnotations}
          renderAnnotation={renderAnnotation}
        />
      </div>
    </section>
  );
}

function RecapDiffTabs({
  files,
  context,
}: {
  files: RecapDiffTabFile[];
  context: RecapRenderContext;
}) {
  const [activePath, setActivePath] = useState(() => files[0]?.path ?? '');

  useEffect(() => {
    const handleFileSelect = (event: Event) => {
      const path = selectedRecapFilePath(event);
      if (path && files.some((file) => file.path === path)) {
        setActivePath(path);
      }
    };

    window.addEventListener(RECAP_FILE_SELECT_EVENT, handleFileSelect);
    return () => window.removeEventListener(RECAP_FILE_SELECT_EVENT, handleFileSelect);
  }, [files]);

  useEffect(() => {
    if (files.length > 0 && !files.some((file) => file.path === activePath)) {
      setActivePath(files[0]!.path);
    }
  }, [activePath, files]);

  const active = files.find((file) => file.path === activePath) ?? files[0];
  if (!active) return null;

  return (
    <section className="my-5">
      <div className="mb-2 flex gap-1 overflow-x-auto rounded-md border border-border bg-surface-hover p-1">
        {files.map((file) => (
          <button
            key={file.path}
            type="button"
            className={`shrink-0 appearance-none rounded-[6px] border-none px-2.5 py-1.5 font-mono text-[0.68rem] transition-colors duration-100 ${
              file.path === active.path
                ? 'bg-surface-active text-text-primary'
                : 'bg-transparent text-text-muted hover:text-text-secondary'
            }`}
            onClick={() => setActivePath(file.path)}
          >
            {file.path}
          </button>
        ))}
      </div>
      <RecapDiffBlock
        path={active.path}
        summary={active.summary}
        annotations={active.annotations}
        context={context}
      />
    </section>
  );
}

function RecapStructuredBlock({
  kind,
  title,
  children,
}: {
  kind: string;
  title?: string;
  children?: string;
}) {
  const payload = extractFencedPayload(children);
  const parsed = parseRecapJSON<unknown>(payload, null);

  return (
    <section className="my-5 rounded-md border border-border bg-surface px-4 py-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="rounded border border-border bg-surface-hover px-1.5 py-0.5 font-mono text-[0.58rem] uppercase tracking-normal text-text-muted">
          {kind}
        </span>
        {title && <h2 className="text-[0.9rem] text-text-primary">{title}</h2>}
      </div>
      {parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (
        <dl className="grid grid-cols-[minmax(120px,0.32fr)_1fr] gap-x-4 gap-y-2">
          {Object.entries(parsed as Record<string, unknown>).map(([key, value]) => (
            <React.Fragment key={key}>
              <dt className="font-mono text-[0.68rem] text-text-muted">{key}</dt>
              <dd className="m-0 min-w-0 break-words font-mono text-[0.72rem] text-text-secondary">
                {typeof value === 'string' ? value : JSON.stringify(value)}
              </dd>
            </React.Fragment>
          ))}
        </dl>
      ) : (
        <pre className="m-0 overflow-auto rounded-md bg-surface-hover p-3 font-mono text-[0.72rem] leading-relaxed text-text-secondary">
          {payload}
        </pre>
      )}
    </section>
  );
}

function normalizeRecapMermaidDiagram(diagram: string): string {
  return diagram
    .split('\n')
    .map((line) => (
      line.replace(
        /(-->|---|==>|-.->|--o|--x)\|([^|"'\n]*@[^|\n]*)\|/g,
        (_match, edge: string, label: string) => (
          `${edge}|"${label.replace(/"/g, '&quot;')}"|`
        ),
      )
    ))
    .join('\n');
}

function RecapMermaidBlock({
  title,
  children,
  resolvedTheme,
}: {
  title?: string;
  children?: string;
  resolvedTheme: 'dark' | 'light';
}) {
  const diagram = children?.trim() ?? '';
  const renderDiagramSource = normalizeRecapMermaidDiagram(diagram);
  const containerRef = useRef<HTMLDivElement>(null);
  const [renderedSvg, setRenderedSvg] = useState<string | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);

  useEffect(() => {
    let isCancelled = false;
    setRenderedSvg(null);
    setRenderError(null);

    if (!diagram) return undefined;

    async function renderDiagram() {
      try {
        const mermaid = (await import('mermaid')).default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          suppressErrorRendering: true,
          theme: resolvedTheme === 'dark' ? 'dark' : 'default',
        });

        const renderId = `recap-mermaid-${hashString(renderDiagramSource)}-${recapMermaidRenderSerial++}`;
        const { svg, bindFunctions } = await mermaid.render(renderId, renderDiagramSource);
        if (isCancelled) return;

        setRenderedSvg(svg);
        window.requestAnimationFrame(() => {
          if (!isCancelled && containerRef.current) {
            bindFunctions?.(containerRef.current);
          }
        });
      } catch (err) {
        if (!isCancelled) {
          setRenderError(err instanceof Error ? err.message : 'Could not render Mermaid diagram.');
        }
      }
    }

    void renderDiagram();

    return () => {
      isCancelled = true;
    };
  }, [diagram, renderDiagramSource, resolvedTheme]);

  return (
    <section className="my-5 rounded-md border border-border bg-surface px-4 py-3" data-recap-mermaid>
      {title && <h2 className="mb-2 text-[0.9rem] text-text-primary">{title}</h2>}
      {renderedSvg ? (
        <div
          ref={containerRef}
          className="overflow-auto rounded-md bg-surface-hover p-3 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full"
          data-recap-mermaid-svg
          dangerouslySetInnerHTML={{ __html: renderedSvg }}
        />
      ) : (
        <div className="rounded-md bg-surface-hover p-3 font-mono text-[0.72rem] leading-relaxed text-text-secondary">
          {renderError ?? 'Rendering diagram...'}
        </div>
      )}
      {renderError && (
        <pre className="mt-3 overflow-auto rounded-md bg-surface-hover p-3 font-mono text-[0.72rem] leading-relaxed text-text-muted">
          {diagram}
        </pre>
      )}
    </section>
  );
}

function RecapToc({
  recapId,
  items,
}: {
  recapId: string;
  items: { id: string; depth: number; text: string }[];
}) {
  if (items.length === 0) return null;

  return (
    <nav className="sticky top-7 hidden self-start xl:block">
      <h2 className="mb-5 font-mono text-[0.68rem] font-semibold uppercase tracking-normal text-text-muted">
        On this recap
      </h2>
      <div className="flex flex-col gap-3">
        {items.map((item) => {
          const href = recapRouteToHash(recapId, item.id);
          return (
            <a
              key={item.id}
              href={href}
              className={`text-[0.82rem] font-semibold no-underline transition-colors duration-100 hover:text-accent ${
                item.depth > 2 ? 'pl-3 text-text-muted' : 'text-text-secondary'
              }`}
              onClick={(event) => {
                if (window.location.hash !== href) return;
                event.preventDefault();
                document.getElementById(item.id)?.scrollIntoView({ block: 'start' });
              }}
            >
              {item.text}
            </a>
          );
        })}
      </div>
    </nav>
  );
}

function renderRecapBlock(block: RecapBlock, index: number, context: RecapRenderContext): React.ReactNode {
  if (block.type === 'heading') {
    const Tag = `h${Math.min(block.depth, 3)}` as 'h1' | 'h2' | 'h3';
    const size = block.depth === 1
      ? 'text-[2.1rem] leading-tight'
      : block.depth === 2
        ? 'text-[1.22rem]'
        : 'text-[0.98rem]';
    return (
      <Tag
        key={index}
        id={recapHeadingId(block, index)}
        className={`scroll-mt-8 mt-8 mb-3 text-text-primary ${size}`}
      >
        {block.text}
      </Tag>
    );
  }
  if (block.type === 'paragraph') {
    return (
      <p key={index} className="my-4 text-[1rem] leading-8 text-text-secondary">
        {renderRecapInline(block.text)}
      </p>
    );
  }
  if (block.type === 'list') {
    const Tag = block.ordered ? 'ol' : 'ul';
    const marker = block.ordered ? 'list-decimal' : 'list-disc';
    return (
      <Tag
        key={index}
        className={`my-4 ${marker} list-outside space-y-2 pl-6 text-[0.92rem] leading-7 text-text-secondary marker:text-accent`}
      >
        {block.items.map((item, itemIndex) => (
          <li key={itemIndex} className="pl-1">
            {renderRecapInline(item)}
          </li>
        ))}
      </Tag>
    );
  }
  if (block.type === 'code') {
    if (block.language.toLowerCase() === 'mermaid') {
      return (
        <RecapMermaidBlock
          key={index}
          resolvedTheme={context.resolvedTheme}
        >
          {block.code}
        </RecapMermaidBlock>
      );
    }
    return (
      <pre key={index} className="my-4 overflow-auto rounded-md border border-border bg-surface-hover p-3 font-mono text-[0.74rem] leading-relaxed text-text-secondary">
        {block.code}
      </pre>
    );
  }
  if (block.type === 'table') {
    return (
      <div key={index} className="my-4 overflow-auto rounded-md border border-border">
        <table className="w-full border-collapse text-left text-[0.82rem] text-text-secondary">
          <thead className="bg-surface-hover text-text-primary">
            <tr>{block.headers.map((header) => <th key={header} className="border-b border-border px-3 py-2 font-semibold">{header}</th>)}</tr>
          </thead>
          <tbody>
            {block.rows.map((row, rowIndex) => (
              <tr key={rowIndex} className="border-b border-border last:border-b-0">
                {block.headers.map((header, cellIndex) => (
                  <td key={`${header}-${cellIndex}`} className="px-3 py-2">{renderRecapInline(row[cellIndex] ?? '')}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (block.name === 'FileMap') {
    return null;
  }
  if (block.name === 'Diff') {
    return (
      <RecapDiffBlock
        key={index}
        path={block.props.path ?? ''}
        summary={block.props.summary}
        annotations={normalizeRecapAnnotations(parseRecapJSON(block.props.annotations, []))}
        context={context}
      />
    );
  }
  if (block.name === 'DiffTabs') {
    return (
      <RecapDiffTabs
        key={index}
        files={normalizeRecapDiffTabs(parseRecapJSON(block.props.files, []))}
        context={context}
      />
    );
  }
  if (block.name === 'Mermaid') {
    return (
      <RecapMermaidBlock
        key={index}
        title={block.props.title}
        resolvedTheme={context.resolvedTheme}
      >
        {block.children}
      </RecapMermaidBlock>
    );
  }
  if (block.name === 'Endpoint') {
    const label = [block.props.method, block.props.path].filter(Boolean).join(' ');
    return <RecapStructuredBlock key={index} kind="endpoint" title={label} children={block.children} />;
  }
  if (block.name === 'DataModel') {
    return <RecapStructuredBlock key={index} kind="data model" title={block.props.title} children={block.children} />;
  }
  if (block.name === 'StateSummary') {
    return <RecapStructuredBlock key={index} kind="state" title={block.props.title} children={block.children} />;
  }
  return null;
}

function RecapView({
  recapId,
  sectionId,
  diffStyle,
  resolvedTheme,
  diffTheme,
}: {
  recapId: string;
  sectionId: string | null;
  diffStyle: DiffStyle;
  resolvedTheme: 'dark' | 'light';
  diffTheme: string;
}) {
  const [recap, setRecap] = useState<RecapResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    setRecap(null);
    setSelectedFilePath(null);

    const loadRecap = async () => {
      try {
        const data = await fetchJSON<RecapResponse>(`/api/recaps/${encodeURIComponent(recapId)}`);
        if (!cancelled) setRecap(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load recap');
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    void loadRecap();
    return () => { cancelled = true; };
  }, [recapId]);

  useEffect(() => {
    const handleFileSelect = (event: Event) => {
      const path = selectedRecapFilePath(event);
      if (path) setSelectedFilePath(path);
    };

    window.addEventListener(RECAP_FILE_SELECT_EVENT, handleFileSelect);
    return () => window.removeEventListener(RECAP_FILE_SELECT_EVENT, handleFileSelect);
  }, []);

  const renderState = useMemo(() => {
    if (!recap) return null;
    try {
      const diffPatches = orderPatchesForTree(splitPatchIntoFiles(recap.patch));
      const fileInfos = diffPatches.map((patch, index) => parseFileInfo(patch, index));
      const patchByPath = new Map(fileInfos.map((info, index) => [info.path, diffPatches[index] ?? '']));
      const blocks = parseRestrictedRecapMdx(recap.mdx);
      const context: RecapRenderContext = {
        fileInfos,
        patchByPath,
        resolvedTheme,
        diffTheme,
        diffStyle,
      };
      return { blocks, context, error: null as string | null };
    } catch (err) {
      return {
        blocks: [] as RecapBlock[],
        context: null,
        error: err instanceof Error ? err.message : 'Could not parse recap MDX',
      };
    }
  }, [diffStyle, diffTheme, recap, resolvedTheme]);

  useEffect(() => {
    if (!sectionId || !renderState?.context) return undefined;
    const frame = window.requestAnimationFrame(() => {
      document.getElementById(sectionId)?.scrollIntoView({ block: 'start' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [renderState, sectionId]);

  if (isLoading) {
    return <p className="m-[1.15rem] text-text-muted text-[0.88rem]">Loading recap...</p>;
  }
  if (error) {
    return <p className="m-[1.15rem] text-danger text-[0.88rem]">{error}</p>;
  }
  if (!recap || !renderState) {
    return <p className="m-[1.15rem] text-text-muted text-[0.88rem]">Recap not found.</p>;
  }
  if (renderState.error || !renderState.context) {
    return <p className="m-[1.15rem] text-danger text-[0.88rem]">{renderState.error}</p>;
  }

  let skippedHeroHeading = false;
  const articleBlocks = renderState.blocks
    .map((block, index) => ({ block, index }))
    .filter(({ block }) => {
      if (!skippedHeroHeading && block.type === 'heading' && block.depth === 1) {
        skippedHeroHeading = true;
        return false;
      }
      return true;
    });
  const tocItems = articleBlocks.flatMap(({ block, index }) => {
    if (block.type !== 'heading' || block.depth === 1 || block.depth > 3) return [];
    return [{ id: recapHeadingId(block, index), depth: block.depth, text: block.text }];
  });
  const documentDiffPaths = recapDocumentDiffPaths(articleBlocks);
  const orderedFileInfos = orderRecapFileInfosForDocument(
    renderState.context.fileInfos,
    documentDiffPaths,
  );
  const explicitlyRenderedDiffPaths = new Set(documentDiffPaths);
  const shouldRenderSelectedDiff =
    selectedFilePath !== null &&
    renderState.context.patchByPath.has(selectedFilePath) &&
    !explicitlyRenderedDiffPaths.has(selectedFilePath);
  const selectedDiffPath = shouldRenderSelectedDiff ? selectedFilePath : null;
  const displayTitle = recapDisplayTitle(recap);
  const articleNodes = articleBlocks
    .filter(({ block }) => !isFileMapBlock(block))
    .map(({ block, index }) => renderRecapBlock(block, index, renderState.context));

  return (
    <div className="flex-1 overflow-auto bg-bg">
      <div className="mx-auto grid max-w-[1760px] grid-cols-1 gap-9 px-6 py-8 xl:grid-cols-[280px_minmax(0,940px)_220px] xl:gap-10 xl:px-10">
        <aside className="order-2 border-t border-border pt-6 xl:sticky xl:top-7 xl:order-1 xl:self-start xl:border-r xl:border-t-0 xl:pr-5 xl:pt-1">
          <RecapFileMap
            fileInfos={orderedFileInfos}
            documentPaths={documentDiffPaths}
            resolvedTheme={resolvedTheme}
          />
        </aside>

        <article className="order-1 min-w-0 xl:order-2">
          <header className="mb-8 border-b border-border pb-7">
            <h1 className="m-0 max-w-[900px] text-[2.65rem] leading-[1.08] text-text-primary max-sm:text-[2rem]">
              {displayTitle}
            </h1>
          </header>
          {articleNodes}
          {selectedDiffPath && (
            <section className="mt-10 border-t border-border pt-8" data-recap-other-files-section>
              <h2 className="mb-5 text-[1.45rem] leading-tight text-text-primary">
                Other files
              </h2>
              <RecapDiffBlock
                key={`selected-file-${selectedDiffPath}`}
                path={selectedDiffPath}
                context={renderState.context}
              />
            </section>
          )}
        </article>

        <div className="order-3">
          <RecapToc recapId={recapId} items={tocItems} />
        </div>
      </div>
    </div>
  );
}

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

function FileFilterMenu({
  query,
  files,
  totalFiles,
  viewedCount,
  deletedCount,
  hideViewed,
  hideDeleted,
  activePath,
  onQueryChange,
  onHideViewedChange,
  onHideDeletedChange,
  onClear,
  onSelectFile,
}: {
  query: string;
  files: FileInfo[];
  totalFiles: number;
  viewedCount: number;
  deletedCount: number;
  hideViewed: boolean;
  hideDeleted: boolean;
  activePath: string | null;
  onQueryChange: (value: string) => void;
  onHideViewedChange: (value: boolean) => void;
  onHideDeletedChange: (value: boolean) => void;
  onClear: () => void;
  onSelectFile: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const hasFilters = query.trim() !== '' || hideViewed || hideDeleted;
  const buttonLabel = hasFilters ? `${files.length}/${totalFiles} files` : `${totalFiles} file${totalFiles === 1 ? '' : 's'}`;

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger
        type="button"
        className={`inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border px-2 font-mono text-[0.68rem] whitespace-nowrap focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
          hasFilters
            ? 'border-accent/30 bg-accent/10 text-accent'
            : 'border-border bg-surface-hover text-text-secondary hover:border-border-strong hover:text-text-primary'
        }`}
        aria-label={`Filter files, ${files.length} of ${totalFiles} shown`}
      >
        <svg className="size-3" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M2.5 3.25h11l-4.25 5v3.5l-2.5 1v-4.5l-4.25-5Z" />
        </svg>
        {buttonLabel}
        <ChevronDownIcon />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner side="bottom" align="end" sideOffset={8} className="z-50">
          <Popover.Popup
            initialFocus={inputRef}
            className="flex max-h-[min(32rem,70vh)] w-[340px] max-w-[calc(100vw-2rem)] origin-[var(--transform-origin)] flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-lg outline-none transition-[opacity,transform] duration-150 data-starting-style:scale-[0.98] data-starting-style:opacity-0 data-ending-style:scale-[0.98] data-ending-style:opacity-0"
          >
            <div className="flex items-center justify-between gap-3 border-b border-border px-3.5 py-2.5">
              <span className="font-mono text-[0.68rem] font-medium text-text-primary">
                Filter changed files
              </span>
              {hasFilters && (
                <button
                  type="button"
                  className="appearance-none border-0 bg-transparent p-0 font-mono text-[0.65rem] text-accent hover:text-text-primary"
                  onClick={onClear}
                >
                  Clear filters
                </button>
              )}
            </div>
            <div className="border-b border-border p-2.5">
              <input
                ref={inputRef}
                type="search"
                className="w-full appearance-none rounded-md border border-border bg-surface-hover px-2.5 py-1.5 font-mono text-[0.72rem] text-text-primary outline-none placeholder:text-text-muted focus:border-accent/40 focus:ring-1 focus:ring-accent/10"
                placeholder="Filter by file path…"
                value={query}
                onChange={(event) => onQueryChange(event.target.value)}
              />
              <div className="mt-2 grid gap-1">
                <label className="flex min-h-8 cursor-pointer items-center gap-2 rounded px-2 font-mono text-[0.7rem] text-text-secondary hover:bg-surface-hover hover:text-text-primary">
                  <input
                    type="checkbox"
                    className="size-3.5 accent-accent"
                    checked={hideViewed}
                    disabled={viewedCount === 0}
                    onChange={(event) => onHideViewedChange(event.target.checked)}
                  />
                  Hide viewed files
                  <span className="ml-auto tabular-nums text-text-muted">{viewedCount}</span>
                </label>
                <label className="flex min-h-8 cursor-pointer items-center gap-2 rounded px-2 font-mono text-[0.7rem] text-text-secondary hover:bg-surface-hover hover:text-text-primary">
                  <input
                    type="checkbox"
                    className="size-3.5 accent-accent"
                    checked={hideDeleted}
                    disabled={deletedCount === 0}
                    onChange={(event) => onHideDeletedChange(event.target.checked)}
                  />
                  Hide deleted files
                  <span className="ml-auto tabular-nums text-text-muted">{deletedCount}</span>
                </label>
              </div>
            </div>
            <div className="min-h-0 overflow-y-auto p-1.5" aria-label="Matching changed files">
              {files.length === 0 ? (
                <p className="m-3 text-center font-mono text-[0.72rem] text-text-muted">
                  No files match these filters.
                </p>
              ) : files.map((file) => (
                <button
                  key={file.path}
                  type="button"
                  className={`flex w-full min-w-0 items-center gap-2 rounded-md border-0 px-2.5 py-2 text-left font-mono hover:bg-surface-hover ${
                    file.path === activePath ? 'bg-accent-bg text-text-primary' : 'bg-transparent text-text-secondary'
                  }`}
                  title={file.path}
                  onClick={() => {
                    onSelectFile(file.path);
                    setOpen(false);
                  }}
                >
                  <span
                    className={`size-1.5 shrink-0 rounded-full ${
                      file.status === 'added'
                        ? 'bg-stat-add'
                        : file.status === 'deleted'
                          ? 'bg-stat-del'
                          : 'bg-accent'
                    }`}
                    aria-hidden="true"
                  />
                  <span className="min-w-0 flex-1 truncate text-[0.7rem]">{file.path}</span>
                  <span className="flex shrink-0 gap-1 text-[0.6rem] tabular-nums">
                    {file.additions > 0 && <span className="text-stat-add">+{file.additions}</span>}
                    {file.deletions > 0 && <span className="text-stat-del">-{file.deletions}</span>}
                  </span>
                </button>
              ))}
            </div>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}

const WORKING_KEY = '__working__';

function diffItemId(key: string): string {
  return `sc-diff-item-${key.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
}

type StackRow = {
  branch: StackBranch;
  stackIndex: number;
  dateLabel: string | null;
};

type StackListProps = {
  rows: StackRow[];
  trunkName: string | null;
  workingStats: WorkingStats | null;
  selection: DiffSelection | null;
  isStackLoading: boolean;
  isGithubInfoLoading: boolean;
  githubInfo: Record<string, GitHubBranchInfo>;
  onSelect: (sel: DiffSelection) => void;
  isFiltering: boolean;
  workingVisible: boolean;
  activeKey: string | null;
  onActivateKey: (key: string) => void;
};

const DATE_PREFIX_RE = /^(\d{4}-\d{2}-\d{2}-)(.+)$/;

function StackList({
  rows,
  trunkName,
  workingStats,
  selection,
  isStackLoading,
  isGithubInfoLoading,
  githubInfo,
  onSelect,
  isFiltering,
  workingVisible,
  activeKey,
  onActivateKey,
}: StackListProps) {
  const activeRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [scrolledToEnd, setScrolledToEnd] = useState(true);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest' });
  }, [activeKey]);

  const updateScrollEnd = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    setScrolledToEnd(el.scrollTop + el.clientHeight >= el.scrollHeight - 4);
  }, []);

  useEffect(() => {
    updateScrollEnd();
  }, [rows, isFiltering, workingVisible, updateScrollEnd]);

  const renderBranchButton = (row: StackRow, index: number | null) => {
    const { branch } = row;
    const active = selection?.type === 'branch' && branch.name === selection.name;
    const isActive = activeKey === branch.name;
    const ghInfo = githubInfo[branch.name];
    const prefixMatch = branch.name.match(DATE_PREFIX_RE);
    const prLabel =
      ghInfo?.prState === 'merged' ? ' merged' : ghInfo?.prIsDraft ? ' draft' : '';
    const prPillCls =
      ghInfo?.prState === 'merged'
        ? 'text-purple-400 bg-purple-400/10 border-purple-400/18'
        : ghInfo?.prIsDraft
          ? 'text-text-muted bg-surface-hover border-border'
          : 'text-green-400 bg-green-400/10 border-green-400/18';

    return (
      <button
        ref={isActive ? activeRef : undefined}
        id={diffItemId(branch.name)}
        role="option"
        aria-selected={active}
        className={`relative appearance-none rounded-md py-[7px] pr-[9px] mx-[8px] text-left text-text-primary cursor-pointer transition-[background] duration-150 ease-in-out border-none ${index === null ? 'pl-[9px]' : 'pl-[28px]'} ${active ? 'bg-accent-bg' : isActive ? 'bg-surface-hover' : 'bg-transparent hover:bg-surface-hover'}`}
        style={index === null ? undefined : ({ anchorName: `--branch-${index}` } as React.CSSProperties)}
        onClick={() => onSelect({ type: 'branch', name: branch.name })}
        onMouseMove={() => { if (!isActive) onActivateKey(branch.name); }}
        type="button"
      >
        {index !== null && (
          <span
            aria-hidden
            className={branch.isCurrent ? 'stack-node stack-node-current' : 'stack-node'}
          />
        )}
        <span className="relative z-[2] flex items-start gap-[8px] w-full min-w-0">
          <span className="min-w-0 flex-1 flex flex-col gap-[3px]">
            <span className="flex items-start gap-[6px] min-w-0">
              <span className="text-[0.86rem] font-semibold leading-5 break-words min-w-0">
                {prefixMatch ? (
                  <>
                    <span className="text-text-muted font-medium">{prefixMatch[1]}</span>
                    {prefixMatch[2]}
                  </>
                ) : (
                  branch.name
                )}
                {isFiltering && (
                  <span className="text-text-muted font-medium"> &rarr; {branch.parent}</span>
                )}
              </span>
              {branch.isCurrent && (
                <span className="font-mono text-[0.58rem] font-medium uppercase tracking-[0.05em] text-accent bg-accent/10 border border-accent/18 px-[5px] py-px rounded-full shrink-0 leading-[1.5] mt-[2px]">
                  current
                </span>
              )}
            </span>
            <span
              className="font-mono text-[0.63rem] text-text-muted leading-4 break-words line-clamp-2"
              title={`${branch.commitShort} ${branch.commitSubject}`}
            >
              {branch.commitShort} {branch.commitSubject}
              {branch.commitCount > 0 && (
                <span className="opacity-75"> &middot; {branch.commitCount} commit{branch.commitCount === 1 ? '' : 's'}</span>
              )}
              {!isFiltering && row.dateLabel && (
                <span className="opacity-75"> &middot; {row.dateLabel}</span>
              )}
            </span>
          </span>
          {isGithubInfoLoading ? (
            <span className="ml-auto flex items-center gap-[5px] shrink-0 mt-[2px]">
              <span className="inline-block w-[32px] h-[14px] rounded-full bg-surface-hover animate-pulse" />
              <span className="inline-block w-[10px] h-[10px] rounded-full bg-surface-hover animate-pulse" />
            </span>
          ) : (ghInfo?.prNumber != null || ghInfo?.checkStatus != null) ? (
            <span className="ml-auto flex items-center gap-[5px] shrink-0 mt-[2px]">
              {ghInfo?.prNumber != null && ghInfo.prUrl && (
                <a
                  href={ghInfo.prUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className={`font-mono text-[0.58rem] font-medium no-underline px-[5px] py-px rounded-full leading-[1.5] whitespace-nowrap border ${prPillCls}`}
                >
                  #{ghInfo.prNumber}{prLabel}
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

  const noResults = isFiltering && !workingVisible && rows.length === 0;

  return (
    <div className="relative flex-1 min-h-0 flex flex-col">
      <div
        id="sc-diff-listbox"
        ref={listRef}
        onScroll={updateScrollEnd}
        className="relative flex flex-col gap-[2px] p-1.5 overflow-y-auto overflow-x-clip flex-1 min-h-0"
        role="listbox"
        aria-label="Tracked stack branches"
      >
        {isStackLoading ? (
          <p className="m-3 text-text-muted text-[0.82rem]">Loading stack…</p>
        ) : null}

        {!isStackLoading && !isFiltering && rows.length === 0 ? (
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
            className={`relative appearance-none rounded-md py-[7px] px-[9px] mx-[8px] mb-1 text-left text-text-primary cursor-pointer transition-[background] duration-150 ease-in-out border-none ${selection?.type === 'working' ? 'bg-accent-bg' : activeKey === WORKING_KEY ? 'bg-surface-hover' : 'bg-transparent hover:bg-surface-hover'}`}
            onClick={() => onSelect({ type: 'working' })}
            onMouseMove={() => { if (activeKey !== WORKING_KEY) onActivateKey(WORKING_KEY); }}
            type="button"
          >
            <span className="relative z-[2] flex items-center gap-[7px] w-full min-w-0">
              <span className="text-[0.82rem] font-semibold whitespace-nowrap overflow-hidden text-ellipsis">
                Working Changes
              </span>
              <span className="font-mono text-[0.58rem] font-medium uppercase tracking-[0.05em] text-text-muted bg-surface-hover border border-border px-[5px] py-px rounded-full shrink-0 leading-[1.5]">
                git diff
              </span>
              {workingStats != null && workingStats.files > 0 && (
                <span className="ml-auto font-mono text-[0.62rem] whitespace-nowrap shrink-0 tabular-nums">
                  <span className="text-stat-add">+{workingStats.additions}</span>{' '}
                  <span className="text-stat-del">&minus;{workingStats.deletions}</span>{' '}
                  <span className="text-text-muted">&middot; {workingStats.files} file{workingStats.files === 1 ? '' : 's'}</span>
                </span>
              )}
            </span>
          </button>
        )}

        {!isFiltering && workingVisible && rows.length > 0 && (
          <div className="border-t border-border -mx-1.5 my-1" />
        )}

        {!isFiltering && trunkName && rows.length > 0 && (
          <div
            aria-hidden
            className="flex items-center gap-[7px] py-[5px] pl-[5px] pr-[9px] mx-[8px]"
          >
            <span className="text-text-muted shrink-0"><GitBranchIcon /></span>
            <span className="text-[0.8rem] font-semibold text-text-secondary min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
              {trunkName}
            </span>
            <span className="font-mono text-[0.58rem] font-medium uppercase tracking-[0.05em] text-text-secondary bg-surface-hover border border-border px-[5px] py-px rounded-full shrink-0 leading-[1.5]">
              trunk
            </span>
          </div>
        )}

        {rows.map((row, index) => {
          const prevRow = index > 0 ? rows[index - 1]! : null;
          const sameStackAsPrev = prevRow != null && row.stackIndex === prevRow.stackIndex;
          return (
            <React.Fragment key={row.branch.name}>
              {!isFiltering && prevRow != null && !sameStackAsPrev && (
                <div className="border-t border-border -mx-1.5 my-[3px]" />
              )}
              {renderBranchButton(row, isFiltering ? null : index)}
              {!isFiltering && sameStackAsPrev && (
                <div
                  aria-hidden
                  className="stack-rail"
                  style={{
                    '--from': `--branch-${index - 1}`,
                    '--to': `--branch-${index}`,
                  } as React.CSSProperties}
                />
              )}
            </React.Fragment>
          );
        })}
      </div>
      <div
        aria-hidden
        className={`pointer-events-none absolute inset-x-px bottom-0 h-8 bg-linear-to-b from-transparent to-surface transition-opacity duration-200 ${scrolledToEnd ? 'opacity-0' : 'opacity-100'}`}
      />
    </div>
  );
}

type DiffSwitcherProps = {
  diff: DiffResponse | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selection: DiffSelection | null;
  branches: StackBranch[];
  workingStats: WorkingStats | null;
  isStackLoading: boolean;
  isGithubInfoLoading: boolean;
  githubInfo: Record<string, GitHubBranchInfo>;
  onSelect: (sel: DiffSelection) => void;
};

const IS_MAC = typeof navigator !== 'undefined' && navigator.platform.includes('Mac');

const KBD_CLS = 'font-mono text-[0.58rem] bg-surface-hover border border-border rounded px-1 py-px leading-none';

function DiffSwitcher({
  diff,
  open,
  onOpenChange,
  selection,
  branches,
  workingStats,
  isStackLoading,
  isGithubInfoLoading,
  githubInfo,
  onSelect,
}: DiffSwitcherProps) {
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

  // Group the pre-ordered branch list into stacks — a stack root is a branch
  // whose parent is not itself tracked (it hangs off the trunk). Then order
  // stacks: the one holding the checked-out branch first, the rest by most
  // recent commit. Stale stack roots carry a last-commit date label.
  const orderedRows = useMemo<StackRow[]>(() => {
    const tracked = new Set(branches.map((b) => b.name));
    const groups: StackBranch[][] = [];
    for (const b of branches) {
      if (!tracked.has(b.parent) || groups.length === 0) groups.push([]);
      groups[groups.length - 1]!.push(b);
    }
    const meta = groups.map((group) => ({
      group,
      isActive: group.some((b) => b.isCurrent),
      lastTime: Math.max(0, ...group.map((b) => b.commitTime ?? 0)),
    }));
    meta.sort(
      (a, b) => Number(b.isActive) - Number(a.isActive) || b.lastTime - a.lastTime,
    );
    return meta.flatMap(({ group, isActive, lastTime }, stackIndex) =>
      group.map((branch, indexInGroup) => ({
        branch,
        stackIndex,
        dateLabel:
          indexInGroup === 0 && !isActive && lastTime > 0
            ? new Date(lastTime * 1000).toLocaleDateString(undefined, {
                month: 'short',
                day: 'numeric',
              })
            : null,
      })),
    );
  }, [branches]);

  const trunkName = useMemo(() => {
    const tracked = new Set(branches.map((b) => b.name));
    const roots = branches.filter((b) => !tracked.has(b.parent));
    if (roots.length === 0) return null;
    const parent = roots[0]!.parent;
    return roots.every((b) => b.parent === parent) ? parent : null;
  }, [branches]);

  const q = query.trim().toLowerCase();
  const tokens = q === '' ? [] : q.split(/\s+/);
  const isFiltering = tokens.length > 0;
  const workingVisible =
    !isFiltering ||
    tokens.every((t) => 'working changes uncommitted changes'.includes(t));
  const filteredRows = isFiltering
    ? orderedRows.filter(({ branch }) => {
        const pr = githubInfo[branch.name]?.prNumber;
        const hay = `${branch.name} ${branch.commitSubject}${pr != null ? ` #${pr}` : ''}`.toLowerCase();
        return tokens.every((t) => hay.includes(t));
      })
    : orderedRows;

  // Flat list of selectable rows, in the same order StackList renders them.
  const items: DiffSelection[] = [
    ...(workingVisible ? [{ type: 'working' as const }] : []),
    ...filteredRows.map(({ branch }) => ({ type: 'branch' as const, name: branch.name })),
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
  }, [query]);

  // On open, start the keyboard highlight on the diff being viewed instead of
  // resetting to the top. The query is always empty here (cleared on close),
  // so indices are relative to the unfiltered list.
  useEffect(() => {
    if (!open) return;
    if (selection?.type === 'branch') {
      const idx = orderedRows.findIndex((r) => r.branch.name === selection.name);
      if (idx >= 0) {
        setActiveIndex(idx + 1); // +1 for the Working Changes row
        return;
      }
    }
    setActiveIndex(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on open
  }, [open]);

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
    } else if (e.key === 'Escape' && query !== '') {
      // First Esc clears the filter; the second closes the popover.
      e.preventDefault();
      e.stopPropagation();
      setQuery('');
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
            className="w-[440px] max-w-[calc(100vw-2rem)] max-h-[min(560px,70vh)] flex flex-col bg-surface border border-border rounded-lg shadow-lg overflow-hidden outline-none origin-[var(--transform-origin)] transition-[opacity,transform] duration-150 data-[starting-style]:opacity-0 data-[starting-style]:scale-[0.98] data-[ending-style]:opacity-0 data-[ending-style]:scale-[0.98]"
          >
            <div className="px-3.5 py-2.5 border-b border-border flex items-center justify-between shrink-0">
              <span className="font-mono text-[0.62rem] font-medium uppercase tracking-[0.13em] text-accent">
                Switch diff
              </span>
              {branches.length > 0 && (
                <span className="font-mono text-[0.6rem] text-text-muted bg-surface-hover border border-border px-2 py-[2px] rounded-full">
                  {isFiltering ? `${filteredRows.length}/${branches.length}` : branches.length} branch{branches.length === 1 ? '' : 'es'}
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
              rows={filteredRows}
              trunkName={trunkName}
              workingStats={workingStats}
              selection={selection}
              isStackLoading={isStackLoading}
              isGithubInfoLoading={isGithubInfoLoading}
              githubInfo={githubInfo}
              onSelect={onSelect}
              isFiltering={isFiltering}
              workingVisible={workingVisible}
              activeKey={activeKey}
              onActivateKey={activateKey}
            />
            <div className="shrink-0 border-t border-border px-3.5 py-[6px] flex items-center gap-3.5 font-mono text-[0.6rem] text-text-muted">
              <span className="flex items-center gap-1"><kbd className={KBD_CLS}>↑↓</kbd> navigate</span>
              <span className="flex items-center gap-1"><kbd className={KBD_CLS}>↵</kbd> select</span>
              <span className="flex items-center gap-1"><kbd className={KBD_CLS}>esc</kbd> clear &middot; close</span>
            </div>
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
  const [recapId, setRecapId] = useState<string | null>(
    () => recapIdFromHash(window.location.hash),
  );
  const [recapSectionId, setRecapSectionId] = useState<string | null>(
    () => recapRouteFromHash(window.location.hash)?.sectionId ?? null,
  );
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [workingPatch, setWorkingPatch] = useState<string | null>(null);
  const [isStackLoading, setIsStackLoading] = useState(true);
  const [isDiffLoading, setIsDiffLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [diffStyle, setDiffStyle] = useState<DiffStyle>('unified');
  const [isPersistedUIStateLoaded, setIsPersistedUIStateLoaded] = useState(false);
  const [persistedViewedFiles, setPersistedViewedFiles] = useState<Record<string, Record<string, string>>>({});
  const [fileFilter, setFileFilter] = useState('');
  const [diffSidebarWidth, setDiffSidebarWidth] = useState(loadDiffSidebarWidth);
  const [isDiffSidebarResizing, setIsDiffSidebarResizing] = useState(false);
  const [activeFileIndex, setActiveFileIndex] = useState<number | null>(null);
  const diffContentRef = useRef<HTMLDivElement>(null);
  const diffSidebarResizeStartRef = useRef<{ clientX: number; width: number } | null>(null);
  const fileRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const fileScrollCleanupRef = useRef<(() => void) | null>(null);
  const scrollSpyFrameRef = useRef<number | null>(null);
  const programmaticFileScrollTopRef = useRef<number | null>(null);
  const viewedScrollAnchorRef = useRef<{ index: number; offset: number } | null>(null);
  const [comments, setComments] = useState<DiffComment[]>([]);
  const [activeInput, setActiveInput] = useState<ActiveInput>(null);
  const [editingComment, setEditingComment] = useState<DiffComment | null>(null);
  const [copyFeedback, setCopyFeedback] = useState(false);
  const [viewedFiles, setViewedFiles] = useState<Set<string>>(new Set());
  const [manuallyCollapsedFiles, setManuallyCollapsedFiles] = useState<Set<string>>(new Set());
  const [expandedViewedFiles, setExpandedViewedFiles] = useState<Set<string>>(new Set());
  const [hideViewedFiles, setHideViewedFiles] = useState(false);
  const [hideDeletedFiles, setHideDeletedFiles] = useState(false);
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
    setRecapId(null);
    setRecapSectionId(null);
    setSelectionRaw((prev) => (diffSelectionsEqual(prev, sel) ? prev : sel));
    if (sel) {
      const newHash = selectionToHash(sel);
      if (window.location.hash !== newHash) {
        window.history.pushState(null, '', newHash);
      }
    }
  }, []);

  useEffect(() => {
    const onHashChange = () => {
      const nextRecapRoute = recapRouteFromHash(window.location.hash);
      if (nextRecapRoute) {
        setRecapId(nextRecapRoute.id);
        setRecapSectionId(nextRecapRoute.sectionId);
        setSelectionRaw(null);
        return;
      }

      setRecapId(null);
      setRecapSectionId(null);
      const sel = selectionFromHash(window.location.hash);
      if (sel) setSelectionRaw((prev) => (diffSelectionsEqual(prev, sel) ? prev : sel));
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

  useEffect(() => {
    localStorage.setItem(DIFF_SIDEBAR_STORAGE_KEY, String(Math.round(diffSidebarWidth)));
  }, [diffSidebarWidth]);

  useEffect(() => {
    const handleWindowResize = () => {
      setDiffSidebarWidth((width) => clampDiffSidebarWidth(width));
    };
    window.addEventListener('resize', handleWindowResize);
    return () => window.removeEventListener('resize', handleWindowResize);
  }, []);

  useEffect(() => {
    if (!isDiffSidebarResizing) return undefined;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    return () => {
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
    };
  }, [isDiffSidebarResizing]);

  const handleDiffSidebarResizeStart = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    diffSidebarResizeStartRef.current = {
      clientX: event.clientX,
      width: diffSidebarWidth,
    };
    setIsDiffSidebarResizing(true);
  }, [diffSidebarWidth]);

  const handleDiffSidebarResizeMove = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const start = diffSidebarResizeStartRef.current;
    if (!start) return;
    setDiffSidebarWidth(clampDiffSidebarWidth(start.width + event.clientX - start.clientX));
  }, []);

  const handleDiffSidebarResizeEnd = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    diffSidebarResizeStartRef.current = null;
    setIsDiffSidebarResizing(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }, []);

  const handleDiffSidebarResizeKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    let nextWidth: number | null = null;
    if (event.key === 'ArrowLeft') nextWidth = diffSidebarWidth - 16;
    if (event.key === 'ArrowRight') nextWidth = diffSidebarWidth + 16;
    if (event.key === 'Home') nextWidth = MIN_DIFF_SIDEBAR_WIDTH;
    if (event.key === 'End') nextWidth = maxDiffSidebarWidth();
    if (nextWidth === null) return;
    event.preventDefault();
    setDiffSidebarWidth(clampDiffSidebarWidth(nextWidth));
  }, [diffSidebarWidth]);

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

    const loadPersistedUIState = async () => {
      try {
        const data = await fetchJSON<PersistedUIStateResponse>('/api/review-state');
        if (cancelled) return;

        if (data.diffStyle === 'unified' || data.diffStyle === 'split') {
          setDiffStyle(data.diffStyle);
        }
        setPersistedViewedFiles(data.viewedFiles ?? {});
      } catch {
        // Persistence is optional; keep the in-memory defaults if it fails.
      } finally {
        if (!cancelled) setIsPersistedUIStateLoaded(true);
      }
    };

    void loadPersistedUIState();
    return () => { cancelled = true; };
  }, []);

  const persistUIStateUpdate = useCallback((update: PersistedUIStateUpdate) => {
    void postPersistedUIStateUpdate(update)
      .then((data) => {
        if (data) setPersistedViewedFiles(data.viewedFiles ?? {});
      })
      .catch(() => {
        // State persistence is a convenience; do not interrupt review on write errors.
      });
  }, []);

  const setAndPersistDiffStyle = useCallback((style: DiffStyle) => {
    setDiffStyle(style);
    persistUIStateUpdate({ diffStyle: style });
  }, [persistUIStateUpdate]);

  useEffect(() => {
    let cancelled = false;

    const loadStack = async () => {
      setIsStackLoading(true);
      setError(null);
      try {
        const data = await fetchJSON<StackResponse>('/api/stack');
        if (cancelled) return;

        setStack(data);

        const hashRecapRoute = recapRouteFromHash(window.location.hash);
        if (hashRecapRoute) {
          setRecapId(hashRecapRoute.id);
          setRecapSectionId(hashRecapRoute.sectionId);
          setSelectionRaw(null);
          return;
        }

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
    setManuallyCollapsedFiles(new Set());
    setExpandedViewedFiles(new Set());
    setExpandedLargeFiles(new Set());
    setComments([]);
    setActiveInput(null);
    setEditingComment(null);
    setIsReviewDialogOpen(false);
    setReviewModelStatus(new Map());
    setReviewSummaries(new Map());
    setReviewFixPrompt(null);
  }, [selection]);

  const activePatch = selection?.type === 'working'
    ? workingPatch
    : selection?.type === 'branch' && diff?.branch === selection.name
      ? diff.patch
      : undefined;

  const diffPatches = useMemo(
    () => orderPatchesForTree(splitPatchIntoFiles(activePatch ?? '')),
    [activePatch],
  );

  const fileInfos = useMemo(
    () => diffPatches.map((patch, i) => parseFileInfo(patch, i)),
    [diffPatches],
  );

  const fileFilterTokens = useMemo(
    () => fileFilter.trim().toLowerCase().split(/\s+/).filter(Boolean),
    [fileFilter],
  );

  const treeFileInfos = useMemo(
    () => fileInfos.filter((info) =>
      !(hideViewedFiles && viewedFiles.has(info.path)) &&
      !(hideDeletedFiles && info.status === 'deleted')
    ),
    [fileInfos, hideDeletedFiles, hideViewedFiles, viewedFiles],
  );

  const visibleDiffEntries = useMemo(
    () => fileInfos.flatMap((info, index) => {
      if (hideViewedFiles && viewedFiles.has(info.path)) return [];
      if (hideDeletedFiles && info.status === 'deleted') return [];
      const normalizedPath = info.path.toLowerCase();
      if (!fileFilterTokens.every((token) => normalizedPath.includes(token))) return [];
      const patch = diffPatches[index];
      return patch ? [{ info, index, patch }] : [];
    }),
    [diffPatches, fileFilterTokens, fileInfos, hideDeletedFiles, hideViewedFiles, viewedFiles],
  );

  const activeFilePath = activeFileIndex == null ? null : fileInfos[activeFileIndex]?.path ?? null;
  const activeTreeFileIndex = activeFilePath == null
    ? null
    : treeFileInfos.findIndex((info) => info.path === activeFilePath);
  const hasActiveFileFilters =
    fileFilterTokens.length > 0 || hideViewedFiles || hideDeletedFiles;
  const reviewProgress = diffPatches.length === 0
    ? 0
    : Math.round((viewedFiles.size / diffPatches.length) * 100);

  const activeDiffVersion = useMemo(() => {
    if (selection?.type === 'branch') {
      const branch = stack?.branches.find((item) => item.name === selection.name);
      return branch ? `${branch.name}:${branch.parent}:${branch.commit}` : null;
    }
    if (selection?.type === 'working') {
      return `working:${hashString(activePatch ?? '')}`;
    }
    return null;
  }, [activePatch, selection, stack]);

  const filePatchKeys = useMemo(
    () => activeDiffVersion == null
      ? new Map<string, string>()
      : new Map(fileInfos.map((info, index) => [info.path, hashString(`${activeDiffVersion}\n${diffPatches[index] ?? ''}`)])),
    [activeDiffVersion, diffPatches, fileInfos],
  );

  const viewedScopeKey = useMemo(
    () => viewedFilesScopeKey(selection),
    [selection],
  );

  useLayoutEffect(() => {
    if (!isPersistedUIStateLoaded) return;
    if (!viewedScopeKey || filePatchKeys.size === 0) {
      setViewedFiles(new Set());
      return;
    }

    const persistedFiles = persistedViewedFiles[viewedScopeKey];
    const restoredViewedFiles = loadPersistedViewedFileSet(persistedFiles, filePatchKeys);
    const restoredFileRecord = buildPersistedViewedFileRecord(restoredViewedFiles, filePatchKeys);
    setViewedFiles(restoredViewedFiles);
    if (!viewedFileRecordsEqual(persistedFiles, restoredFileRecord)) {
      setPersistedViewedFiles((prev) => {
        const next = { ...prev };
        if (Object.keys(restoredFileRecord).length > 0) {
          next[viewedScopeKey] = restoredFileRecord;
        } else {
          delete next[viewedScopeKey];
        }
        return next;
      });
      persistUIStateUpdate({
        viewedScope: viewedScopeKey,
        viewedFiles: restoredFileRecord,
      });
    }
  }, [filePatchKeys, isPersistedUIStateLoaded, persistedViewedFiles, persistUIStateUpdate, viewedScopeKey]);

  const persistViewedFiles = useCallback(
    (nextViewedFiles: Set<string>) => {
      if (!viewedScopeKey || filePatchKeys.size === 0) return;
      const viewedFileRecord = buildPersistedViewedFileRecord(nextViewedFiles, filePatchKeys);
      setPersistedViewedFiles((prev) => {
        const next = { ...prev };
        if (Object.keys(viewedFileRecord).length > 0) {
          next[viewedScopeKey] = viewedFileRecord;
        } else {
          delete next[viewedScopeKey];
        }
        return next;
      });
      persistUIStateUpdate({
        viewedScope: viewedScopeKey,
        viewedFiles: viewedFileRecord,
      });
    },
    [filePatchKeys, persistUIStateUpdate, viewedScopeKey],
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

  const captureFileScrollAnchor = useCallback((path: string, willCollapse: boolean) => {
    const scroller = diffContentRef.current;
    if (!scroller) return;

    const index = fileInfos.findIndex((info) => info.path === path);
    if (index === -1) return;

    const anchor = fileRefs.current[index];
    if (!anchor) return;

    const offset = anchor.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
    viewedScrollAnchorRef.current = {
      index,
      offset: willCollapse ? Math.max(offset, 0) : offset,
    };
  }, [fileInfos]);

  const toggleViewed = useCallback((path: string) => {
    fileScrollCleanupRef.current?.();
    fileScrollCleanupRef.current = null;
    const willMarkViewed = !viewedFiles.has(path);
    captureFileScrollAnchor(path, willMarkViewed);
    setManuallyCollapsedFiles((prev) => {
      if (!prev.has(path)) return prev;
      const next = new Set(prev);
      next.delete(path);
      return next;
    });
    setExpandedViewedFiles((prev) => {
      if (!prev.has(path)) return prev;
      const next = new Set(prev);
      next.delete(path);
      return next;
    });
    setViewedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      persistViewedFiles(next);
      return next;
    });
  }, [captureFileScrollAnchor, persistViewedFiles, viewedFiles]);

  const toggleFileCollapsed = useCallback((path: string) => {
    const isViewed = viewedFiles.has(path);
    const isCollapsed = manuallyCollapsedFiles.has(path) ||
      (isViewed && !expandedViewedFiles.has(path));
    captureFileScrollAnchor(path, !isCollapsed);

    if (isViewed) {
      setExpandedViewedFiles((prev) => {
        const next = new Set(prev);
        if (isCollapsed) next.add(path);
        else next.delete(path);
        return next;
      });
      return;
    }

    setManuallyCollapsedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, [captureFileScrollAnchor, expandedViewedFiles, manuallyCollapsedFiles, viewedFiles]);

  useLayoutEffect(() => {
    const anchor = viewedScrollAnchorRef.current;
    viewedScrollAnchorRef.current = null;
    if (!anchor) return;

    const scroller = diffContentRef.current;
    const target = fileRefs.current[anchor.index];
    if (!scroller || !target) return;

    const nextOffset = target.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
    const delta = nextOffset - anchor.offset;
    if (Math.abs(delta) > 0.5) {
      scroller.scrollTop += delta;
    }
  }, [expandedViewedFiles, manuallyCollapsedFiles, viewedFiles]);

  const alignFileInDiffPane = useCallback((index: number): boolean => {
    const scroller = diffContentRef.current;
    const target = fileRefs.current[index];
    if (!scroller || !target) return false;

    const scrollerRect = scroller.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const nextScrollTop = scroller.scrollTop + targetRect.top - scrollerRect.top;
    programmaticFileScrollTopRef.current = nextScrollTop;
    scroller.scrollTo({
      top: nextScrollTop,
      behavior: 'auto',
    });
    return true;
  }, []);

  const scrollToFile = useCallback((index: number) => {
    const info = fileInfos[index];
    if (info && viewedFiles.has(info.path)) {
      setExpandedViewedFiles((prev) => {
        const next = new Set(prev);
        next.add(info.path);
        return next;
      });
    }
    if (info && manuallyCollapsedFiles.has(info.path)) {
      setManuallyCollapsedFiles((prev) => {
        const next = new Set(prev);
        next.delete(info.path);
        return next;
      });
    }
    setActiveFileIndex(index);

    fileScrollCleanupRef.current?.();
    fileScrollCleanupRef.current = null;

    requestAnimationFrame(() => {
      const scroller = diffContentRef.current;
      if (!scroller || !alignFileInDiffPane(index) || typeof ResizeObserver === 'undefined') {
        return;
      }

      let lastAlignedScrollTop = scroller.scrollTop;
      let frameId: number | null = null;
      let timeoutId: number;
      let cleanup: () => void;
      const observer = new ResizeObserver(() => {
        if (frameId !== null) cancelAnimationFrame(frameId);
        frameId = requestAnimationFrame(() => {
          frameId = null;
          if (Math.abs(scroller.scrollTop - lastAlignedScrollTop) > 1) {
            cleanup();
            return;
          }
          if (alignFileInDiffPane(index)) {
            lastAlignedScrollTop = scroller.scrollTop;
          }
        });
      });
      cleanup = () => {
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
  }, [alignFileInDiffPane, fileInfos, manuallyCollapsedFiles, viewedFiles]);

  const scrollToFilePath = useCallback((path: string) => {
    const index = fileInfos.findIndex((info) => info.path === path);
    if (index >= 0) scrollToFile(index);
  }, [fileInfos, scrollToFile]);

  const handleTreeFileClick = useCallback((index: number) => {
    const path = treeFileInfos[index]?.path;
    if (path) scrollToFilePath(path);
  }, [scrollToFilePath, treeFileInfos]);

  const syncActiveFileFromScroll = useCallback(() => {
    const scroller = diffContentRef.current;
    if (!scroller || visibleDiffEntries.length === 0) {
      setActiveFileIndex(null);
      return;
    }

    const threshold = scroller.getBoundingClientRect().top + 1;
    const nextActive = visibleDiffEntries.find(({ index }) => {
      const section = fileRefs.current[index];
      return section ? section.getBoundingClientRect().bottom > threshold : false;
    });
    setActiveFileIndex((current) =>
      current === (nextActive?.index ?? null) ? current : nextActive?.index ?? null
    );
  }, [visibleDiffEntries]);

  const handleDiffScroll = useCallback(() => {
    const expectedScrollTop = programmaticFileScrollTopRef.current;
    const actualScrollTop = diffContentRef.current?.scrollTop;
    const isProgrammaticScroll =
      expectedScrollTop !== null &&
      actualScrollTop !== undefined &&
      Math.abs(expectedScrollTop - actualScrollTop) <= 1;
    programmaticFileScrollTopRef.current = null;
    if (!isProgrammaticScroll) {
      fileScrollCleanupRef.current?.();
      fileScrollCleanupRef.current = null;
    }
    if (scrollSpyFrameRef.current !== null) return;
    scrollSpyFrameRef.current = window.requestAnimationFrame(() => {
      scrollSpyFrameRef.current = null;
      syncActiveFileFromScroll();
    });
  }, [syncActiveFileFromScroll]);

  useLayoutEffect(() => {
    const frame = window.requestAnimationFrame(syncActiveFileFromScroll);
    return () => window.cancelAnimationFrame(frame);
  }, [syncActiveFileFromScroll]);

  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(syncActiveFileFromScroll);
    for (const { index } of visibleDiffEntries) {
      const section = fileRefs.current[index];
      if (section) observer.observe(section);
    }
    return () => observer.disconnect();
  }, [syncActiveFileFromScroll, visibleDiffEntries]);

  useEffect(() => {
    return () => {
      fileScrollCleanupRef.current?.();
      if (scrollSpyFrameRef.current !== null) {
        window.cancelAnimationFrame(scrollSpyFrameRef.current);
      }
    };
  }, []);

  const handleRangeSelected = useCallback(
    (file: string, startLine: number, endLine: number, side: AnnotationSide) => {
      setEditingComment(null);
      setActiveInput({ file, startLine, endLine, side });
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
          } else if (data.workingDiffKey !== previousWorkingDiffKey) {
            // Keep the switcher's working-tree stats fresh even when the
            // stack itself didn't change and no diff refetch is needed.
            setStack(data);
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
  }, []);

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
  const isRecapRoute = recapId !== null;

  return (
    <WorkerPoolContextProvider
      poolOptions={{ workerFactory: () => new DiffsWorker(), poolSize: 4 }}
      highlighterOptions={{}}
    >
    <main className="relative isolate flex h-dvh flex-col overflow-hidden antialiased animate-fade-in">
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
          <div className="flex items-center gap-2.5 min-w-0 max-w-[58vw] shrink-0 xl:max-w-none">
            <img
              src="/favicon.svg"
              alt="Shortcake"
              className="hidden sm:block h-[34px] w-[34px] shrink-0"
              width="97"
              height="99"
            />
            <div className="hidden sm:block h-7 w-px bg-border shrink-0" aria-hidden="true" />
            {isRecapRoute ? (
              <div className="inline-flex min-w-0 max-w-full items-center gap-2.5 rounded-md border border-border bg-surface-hover px-2.5 py-1.5">
                <span className="text-accent shrink-0"><GitBranchIcon /></span>
                <span className="flex min-w-0 flex-col leading-tight">
                  <span className="font-mono text-[0.56rem] font-medium uppercase tracking-normal text-text-muted">
                    Viewing recap
                  </span>
                  <span className="truncate font-mono text-[0.82rem] font-semibold text-text-primary">
                    {recapId}
                  </span>
                </span>
              </div>
            ) : (
              <DiffSwitcher
                diff={diff}
                open={switcherOpen}
                onOpenChange={setSwitcherOpen}
                selection={selection}
                branches={branches}
                workingStats={stack?.workingStats ?? null}
                isStackLoading={isStackLoading}
                isGithubInfoLoading={isGithubInfoLoading}
                githubInfo={githubInfo}
                onSelect={(sel) => { setSelection(sel); setSwitcherOpen(false); }}
              />
            )}
          </div>

          <div className="flex items-center gap-2 min-w-0 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {!isRecapRoute && selection && !isDiffLoading && diffPatches.length > 0 && (
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
            {!isRecapRoute && comments.length > 0 && (
              <button
                type="button"
                className="appearance-none border border-accent bg-accent/10 text-accent text-[0.7rem] font-mono px-2.5 py-1 rounded-md cursor-pointer hover:bg-accent/20 transition-colors duration-100 whitespace-nowrap"
                onClick={handleCopyComments}
              >
                {copyFeedback ? 'Copied!' : `Copy ${comments.length} comment${comments.length === 1 ? '' : 's'}`}
              </button>
            )}
            {!isRecapRoute && !isDiffLoading && diffPatches.length > 0 && (
              <div
                className="hidden h-7 items-center gap-2 rounded-md border border-border bg-surface-hover px-2.5 sm:flex"
                aria-label={`${viewedFiles.size} of ${diffPatches.length} files viewed`}
              >
                <span className="font-mono text-[0.65rem] tabular-nums text-text-secondary whitespace-nowrap">
                  {viewedFiles.size}/{diffPatches.length} viewed
                </span>
                <span className="h-1.5 w-14 overflow-hidden rounded-full bg-surface-active" aria-hidden="true">
                  <span
                    className="block h-full w-(--review-progress) rounded-full bg-accent"
                    style={{ '--review-progress': `${reviewProgress}%` } as React.CSSProperties}
                  />
                </span>
              </div>
            )}
            {!isRecapRoute && !isDiffLoading && diffPatches.length > 0 && (
              <FileFilterMenu
                query={fileFilter}
                files={visibleDiffEntries.map(({ info }) => info)}
                totalFiles={fileInfos.length}
                viewedCount={viewedFiles.size}
                deletedCount={fileInfos.filter((info) => info.status === 'deleted').length}
                hideViewed={hideViewedFiles}
                hideDeleted={hideDeletedFiles}
                activePath={activeFilePath}
                onQueryChange={setFileFilter}
                onHideViewedChange={setHideViewedFiles}
                onHideDeletedChange={setHideDeletedFiles}
                onClear={() => {
                  setFileFilter('');
                  setHideViewedFiles(false);
                  setHideDeletedFiles(false);
                }}
                onSelectFile={scrollToFilePath}
              />
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
                onClick={() => setAndPersistDiffStyle('unified')}
                type="button"
              >
                Unified
              </button>
              <button
                className={`appearance-none border-none rounded-[6px] font-mono text-[0.7rem] tracking-[0.02em] px-2.5 py-1 cursor-pointer transition-[color,background] duration-[120ms] ease-in-out ${diffStyle === 'split' ? 'text-text-primary bg-surface-active' : 'bg-transparent text-text-muted hover:text-text-secondary'}`}
                onClick={() => setAndPersistDiffStyle('split')}
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

        {isRecapRoute && recapId ? (
          <RecapView
            recapId={recapId}
            sectionId={recapSectionId}
            diffStyle={diffStyle}
            resolvedTheme={resolvedTheme}
            diffTheme={activeDiffTheme}
          />
        ) : null}

        {!isRecapRoute && error ? (
          <p className="m-[1.15rem] text-danger text-[0.88rem]">{error}</p>
        ) : null}

        {!isRecapRoute && isDiffLoading ? (
          <p className="m-[1.15rem] text-text-muted text-[0.88rem]">Loading diff…</p>
        ) : null}

        {!isRecapRoute && !isDiffLoading && activePatch !== undefined && activePatch !== null && activePatch.trim() === '' ? (
          <p className="m-[1.15rem] text-text-muted text-[0.88rem]">
            {selection?.type === 'working'
              ? 'No uncommitted changes.'
              : 'No file differences between this branch and its parent.'}
          </p>
        ) : null}

        {!isRecapRoute && !isDiffLoading && activePatch && activePatch.trim() !== '' && diffPatches.length === 0 ? (
          <p className="m-[1.15rem] text-danger text-[0.88rem]">
            Could not render this diff patch.
          </p>
        ) : null}

        {!isRecapRoute && !isDiffLoading && activePatch && diffPatches.length > 0 && (
          <div className="flex flex-1 min-h-0">
            {isWideScreen && (
            <>
            <aside
              data-testid="diff-sidebar"
              className="flex shrink-0 flex-col overflow-hidden max-[1100px]:hidden"
              style={{ width: `${diffSidebarWidth}px` }}
            >
              <div className="px-4 py-3 border-b border-border">
                <div className="flex items-center justify-between">
                  <span className="text-[0.8rem] font-semibold text-text-primary">
                    Files changed
                  </span>
                  <span className="text-[0.65rem] font-mono tabular-nums text-text-muted bg-surface-hover px-2 py-0.5 rounded-full">
                    {hasActiveFileFilters ? `${visibleDiffEntries.length}/${fileInfos.length}` : fileInfos.length}
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
                fileInfos={treeFileInfos}
                fileFilter={fileFilter}
                activeFileIndex={activeTreeFileIndex === -1 ? null : activeTreeFileIndex}
                viewedFiles={viewedFiles}
                resolvedTheme={resolvedTheme}
                onFilterChange={setFileFilter}
                onFileClick={handleTreeFileClick}
              />
            </aside>
            <div
              role="separator"
              aria-label="Resize files sidebar"
              aria-orientation="vertical"
              aria-valuemin={MIN_DIFF_SIDEBAR_WIDTH}
              aria-valuemax={maxDiffSidebarWidth()}
              aria-valuenow={Math.round(diffSidebarWidth)}
              tabIndex={0}
              title="Drag to resize · Double-click to reset"
              className={`group relative z-20 w-px shrink-0 touch-none cursor-col-resize bg-border outline-none transition-colors after:absolute after:inset-y-0 after:-left-1 after:w-[9px] hover:bg-accent/60 focus-visible:bg-accent max-[1100px]:hidden ${isDiffSidebarResizing ? 'bg-accent' : ''}`}
              onDoubleClick={() => setDiffSidebarWidth(clampDiffSidebarWidth(DEFAULT_DIFF_SIDEBAR_WIDTH))}
              onKeyDown={handleDiffSidebarResizeKeyDown}
              onPointerDown={handleDiffSidebarResizeStart}
              onPointerMove={handleDiffSidebarResizeMove}
              onPointerUp={handleDiffSidebarResizeEnd}
              onPointerCancel={handleDiffSidebarResizeEnd}
            >
              <span
                aria-hidden="true"
                className={`pointer-events-none absolute top-1/2 left-1/2 h-9 w-[3px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent transition-opacity ${isDiffSidebarResizing ? 'opacity-100' : 'opacity-0 group-hover:opacity-70 group-focus-visible:opacity-100'}`}
              />
            </div>
            </>
            )}

            <div
              ref={diffContentRef}
              className="diff-content flex-1 min-w-0 overflow-auto"
              onScroll={handleDiffScroll}
            >
              {reviewSummaries.size > 0 && (
                <ReviewSummaryPanel
                  summaries={reviewSummaries}
                  fixPrompt={reviewFixPrompt}
                  onClose={() => { setReviewSummaries(new Map()); setReviewFixPrompt(null); }}
                />
              )}
              {visibleDiffEntries.length === 0 && (
                <div className="flex min-h-48 flex-col items-center justify-center gap-3 px-6 text-center">
                  <p className="m-0 font-mono text-[0.78rem] text-text-secondary">
                    No changed files match these filters.
                  </p>
                  <button
                    type="button"
                    className="h-7 rounded-md border border-border bg-surface-hover px-3 font-mono text-[0.7rem] text-text-secondary hover:border-border-strong hover:text-text-primary"
                    onClick={() => {
                      setFileFilter('');
                      setHideViewedFiles(false);
                      setHideDeletedFiles(false);
                    }}
                  >
                    Clear filters
                  </button>
                </div>
              )}
              {visibleDiffEntries.map(({ patch, info, index }, visibleIndex) => {
                const isViewed = viewedFiles.has(info.path);
                const isCollapsed = manuallyCollapsedFiles.has(info.path) ||
                  (isViewed && !expandedViewedFiles.has(info.path));
                const isLastVisibleFile = visibleIndex === visibleDiffEntries.length - 1;
                return (
                  <div
                    className={[
                      visibleIndex > 0 ? 'border-t-2 border-guide' : '',
                      isLastVisibleFile ? 'min-h-full' : '',
                    ].filter(Boolean).join(' ') || undefined}
                    key={`${selection?.type === 'working' ? 'working' : diff?.branch}-${info.path}`}
                    data-file-path={info.path}
                    data-file-index={index}
                    data-file-collapsed={isCollapsed ? 'true' : 'false'}
                    ref={(el) => { fileRefs.current[index] = el; }}
                  >
                    {isCollapsed ? (
                      <DiffFileSection
                        patch={patch}
                        fileInfo={info}
                        fileComments={commentsByFile.get(info.path) ?? EMPTY_COMMENTS}
                        activeInput={activeInput}
                        editingComment={editingComment}
                        onRangeSelected={handleRangeSelected}
                        onStartEdit={handleStartEdit}
                        onAddComment={handleAddComment}
                        onUpdateComment={handleUpdateComment}
                        onDeleteComment={handleDeleteComment}
                        onCancelInput={handleCancelInput}
                        diffStyle={diffStyle}
                        resolvedTheme={resolvedTheme}
                        diffTheme={activeDiffTheme}
                        isCollapsed
                        isViewed={isViewed}
                        onToggleCollapsed={toggleFileCollapsed}
                        onToggleViewed={toggleViewed}
                      />
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
                          onRangeSelected={handleRangeSelected}
                          onStartEdit={handleStartEdit}
                          onAddComment={handleAddComment}
                          onUpdateComment={handleUpdateComment}
                          onDeleteComment={handleDeleteComment}
                          onCancelInput={handleCancelInput}
                          diffStyle={diffStyle}
                          resolvedTheme={resolvedTheme}
                          diffTheme={activeDiffTheme}
                          isCollapsed={false}
                          isViewed={isViewed}
                          onToggleCollapsed={toggleFileCollapsed}
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

      {!isRecapRoute && isReviewDialogOpen && (
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
