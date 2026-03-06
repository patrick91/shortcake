import {
  PatchDiff,
  type PatchDiffProps,
  type DiffLineAnnotation,
  type AnnotationSide,
  type SelectedLineRange,
  WorkerPoolContextProvider,
} from '@pierre/diffs/react';
import DiffsWorker from '@pierre/diffs/worker/worker.js?worker';
import React, { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, useSyncExternalStore, useTransition } from 'react';
import { Group, Panel, Separator, useDefaultLayout } from 'react-resizable-panels';

type DiffStyle = 'unified' | 'split';
type ThemeMode = 'dark' | 'light' | 'system';

type DiffComment = {
  id: string;
  file: string;
  startLine: number;
  endLine: number;
  side: AnnotationSide;
  text: string;
};

type CommentMeta = {
  commentId: string;
  text: string;
  isInput: boolean;
  isToolbar?: boolean;
  isHunkToggle?: boolean;
  hunkKey?: HunkKey;
  isSelected?: boolean;
  hunkContext?: string | null;
  isSplitSelection?: boolean;
  splitSelectionId?: string;
};

type MoveHunksResponse = {
  sourceBranch: string;
  targetBranch: string;
  filePaths: string[];
  restackedBranches: string[];
};

type AcceptHunksResponse = {
  targetBranch: string;
  filePaths: string[];
  restackedBranches: string[];
};

type SplitHunksResponse = {
  sourceBranch: string;
  newBranch: string;
  placement: string;
  filePaths: string[];
  restackedBranches: string[];
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

type ParsedHunk = {
  file: string;
  patchIndex: number;
  hunkIndex: number;
  firstChangedLine: number;
  hunkStartLine: number;
  hunkContext: string | null;
  side: AnnotationSide;
};

type HunkKey = `${string}:${number}`;

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

type HunkSuggestionItem = {
  file: string;
  hunkIndex: number;
  suggestedBranch: string | null;
  reason: string;
};

type SuggestionsResponse = {
  suggestions: HunkSuggestionItem[];
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

type FileInfo = {
  path: string;
  name: string;
  additions: number;
  deletions: number;
  patchIndex: number;
};

type DirEntry = {
  type: 'dir';
  name: string;
  path: string;
  children: TreeEntry[];
};

type FileEntry = {
  type: 'file';
  name: string;
  info: FileInfo;
};

type TreeEntry = DirEntry | FileEntry;

const API_BASE = import.meta.env.VITE_SHORTCAKE_API_URL ?? '';
const FILE_TREE_INDENT_BASE = 8;
const FILE_TREE_INDENT_STEP = 10;
const STACK_CARD_INDENT_BASE = 4;
const STACK_CARD_INDENT_STEP = 10;
const STACK_GUIDE_OFFSET = 6;
const STACK_GUIDE_STEP = 10;

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

  return { path, name, additions, deletions, patchIndex: index };
}

function parseHunksFromPatch(patch: string, fileInfo: FileInfo, patchIndex: number): ParsedHunk[] {
  const hunks: ParsedHunk[] = [];
  const lines = patch.split('\n');
  let hunkIndex = 0;

  for (const line of lines) {
    const match = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)/);
    if (!match) continue;

    const newStart = parseInt(match[2]!, 10);
    const contextStr = match[3]?.trim() || null;

    // Find the first changed line in this hunk
    let currentLine = newStart;
    let firstChangedLine = newStart;
    let foundChange = false;
    const hunkLineStart = lines.indexOf(line) + 1;

    for (let i = hunkLineStart; i < lines.length; i++) {
      const hunkLine = lines[i]!;
      if (hunkLine.startsWith('@@')) break; // next hunk
      if (hunkLine.startsWith('+') && !hunkLine.startsWith('+++')) {
        if (!foundChange) {
          firstChangedLine = currentLine;
          foundChange = true;
        }
        currentLine++;
      } else if (hunkLine.startsWith('-') && !hunkLine.startsWith('---')) {
        if (!foundChange) {
          firstChangedLine = currentLine;
          foundChange = true;
        }
        // deletions don't advance new line counter
      } else {
        currentLine++;
      }
    }

    hunks.push({
      file: fileInfo.path,
      patchIndex,
      hunkIndex,
      firstChangedLine: foundChange ? firstChangedLine : newStart,
      hunkStartLine: newStart,
      hunkContext: contextStr,
      side: 'additions',
    });
    hunkIndex++;
  }

  return hunks;
}

function buildFileTree(files: FileInfo[]): TreeEntry[] {
  const root: TreeEntry[] = [];

  for (const file of files) {
    const parts = file.path.split('/');
    let current = root;

    for (let i = 0; i < parts.length - 1; i++) {
      const dirName = parts[i]!;
      const dirPath = parts.slice(0, i + 1).join('/');

      let dir = current.find(
        (e): e is DirEntry => e.type === 'dir' && e.name === dirName,
      );

      if (!dir) {
        dir = { type: 'dir', name: dirName, path: dirPath, children: [] };
        current.push(dir);
      }

      current = dir.children;
    }

    current.push({ type: 'file', name: file.name, info: file });
  }

  function sortEntries(entries: TreeEntry[]): void {
    entries.sort((a, b) => {
      if (a.type !== b.type) return a.type === 'dir' ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    for (const entry of entries) {
      if (entry.type === 'dir') sortEntries(entry.children);
    }
  }

  sortEntries(root);
  return root;
}

function treeHasMatch(entry: TreeEntry, filter: string): boolean {
  if (entry.type === 'file') {
    return entry.info.path.toLowerCase().includes(filter);
  }
  return entry.children.some((child) => treeHasMatch(child, filter));
}


function FileTreeEntries({
  entries,
  depth,
  collapsedDirs,
  onToggleDir,
  activeIndex,
  onFileClick,
  filter,
  viewedFiles,
}: {
  entries: TreeEntry[];
  depth: number;
  collapsedDirs: Set<string>;
  onToggleDir: (path: string) => void;
  activeIndex: number | null;
  onFileClick: (index: number) => void;
  filter: string;
  viewedFiles?: Set<string>;
}) {
  const lowerFilter = filter.toLowerCase();

  return (
    <>
      {entries.map((entry) => {
        if (entry.type === 'dir') {
          if (lowerFilter && !treeHasMatch(entry, lowerFilter)) return null;
          const collapsed = collapsedDirs.has(entry.path);

          return (
            <div key={entry.path}>
              <button
                className="appearance-none border-none bg-transparent text-text-secondary flex items-center gap-[5px] w-full py-1 px-2.5 font-sans text-[0.78rem] font-semibold cursor-pointer select-none transition-[background] duration-100 ease-in-out hover:bg-surface-hover"
                style={{
                  paddingInlineStart: `${FILE_TREE_INDENT_BASE + depth * FILE_TREE_INDENT_STEP}px`,
                }}
                onClick={() => onToggleDir(entry.path)}
                type="button"
              >
                <span
                  className={`inline-block w-3.5 text-center text-[0.65rem] text-text-muted transition-transform duration-150 ease-in-out shrink-0 ${collapsed ? '-rotate-90' : ''}`}
                >
                  &#9662;
                </span>
                <span className="whitespace-nowrap overflow-hidden text-ellipsis">
                  {entry.name}
                </span>
              </button>
              {!collapsed && (
                <FileTreeEntries
                  entries={entry.children}
                  depth={depth + 1}
                  collapsedDirs={collapsedDirs}
                  onToggleDir={onToggleDir}
                  activeIndex={activeIndex}
                  onFileClick={onFileClick}
                  filter={filter}
                  viewedFiles={viewedFiles}
                />
              )}
            </div>
          );
        }

        if (lowerFilter && !entry.info.path.toLowerCase().includes(lowerFilter)) {
          return null;
        }

        const active = entry.info.patchIndex === activeIndex;
        const isViewed = viewedFiles?.has(entry.info.path) ?? false;

        return (
          <button
            key={entry.info.path}
            className={`appearance-none border-none bg-transparent flex items-center gap-1.5 w-full py-[3px] px-2.5 font-mono text-[0.72rem] cursor-pointer select-none transition-[background,color,opacity] duration-100 ease-in-out ${active ? 'bg-accent-bg text-text-primary' : 'text-text-secondary hover:bg-surface-hover hover:text-text-primary'}`}
            style={{
              paddingInlineStart: `${FILE_TREE_INDENT_BASE + depth * FILE_TREE_INDENT_STEP}px`,
              opacity: isViewed ? 0.5 : 1,
            }}
            onClick={() => onFileClick(entry.info.patchIndex)}
            type="button"
          >
            {isViewed && (
              <span className="text-accent text-[0.6rem] shrink-0">{'\u2713'}</span>
            )}
            <span className="whitespace-nowrap overflow-hidden text-ellipsis">
              {entry.name}
            </span>
            <span className="ml-auto flex gap-[5px] text-[0.65rem] shrink-0">
              {entry.info.additions > 0 && (
                <span className="text-stat-add">+{entry.info.additions}</span>
              )}
              {entry.info.deletions > 0 && (
                <span className="text-stat-del">-{entry.info.deletions}</span>
              )}
            </span>
          </button>
        );
      })}
    </>
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

function ViewedFileHeader({
  fileInfo,
  isViewed,
  onToggle,
}: {
  fileInfo: FileInfo;
  isViewed: boolean;
  onToggle: (path: string) => void;
}) {
  return (
    <div
      className={`flex items-center gap-2 px-3 py-1.5 cursor-pointer select-none transition-colors duration-100 ${
        isViewed
          ? 'bg-surface-hover/60 hover:bg-surface-hover'
          : 'bg-transparent hover:bg-surface-hover/40'
      }`}
      onClick={() => onToggle(fileInfo.path)}
    >
      <span
        className={`inline-flex items-center justify-center w-4 h-4 rounded border text-[0.6rem] shrink-0 transition-colors duration-100 ${
          isViewed
            ? 'bg-accent/15 border-accent/40 text-accent'
            : 'border-border text-transparent hover:border-border-strong'
        }`}
      >
        {isViewed ? '\u2713' : ''}
      </span>
      <span
        className={`font-mono text-[0.72rem] truncate transition-opacity duration-100 ${
          isViewed ? 'text-text-muted opacity-60' : 'text-text-secondary'
        }`}
      >
        {fileInfo.path}
      </span>
      {isViewed && (
        <span className="ml-auto flex gap-[5px] text-[0.6rem] shrink-0 opacity-50">
          {fileInfo.additions > 0 && (
            <span className="text-stat-add">+{fileInfo.additions}</span>
          )}
          {fileInfo.deletions > 0 && (
            <span className="text-stat-del">-{fileInfo.deletions}</span>
          )}
        </span>
      )}
      {!isViewed && (
        <span className="ml-auto font-mono text-[0.6rem] text-text-muted opacity-0 group-hover:opacity-100">
          Mark as viewed
        </span>
      )}
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
      <div className="flex items-center gap-2 px-3 py-1.5 bg-surface-hover/40">
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
      <div className="flex items-center gap-2 px-3 py-1.5 bg-surface-hover/40">
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

function HunkToggle({
  hunkKey,
  isSelected,
  onToggle,
  hunkContext,
}: {
  hunkKey: HunkKey;
  isSelected: boolean;
  onToggle: (key: HunkKey) => void;
  hunkContext: string | null;
}) {
  return (
    <div
      className={`w-full flex items-center gap-2 py-1.5 px-3 cursor-pointer select-none transition-colors duration-100 border-b ${
        isSelected
          ? 'bg-accent/10 border-b-accent/30'
          : 'bg-surface-hover/50 border-b-border hover:bg-surface-hover'
      }`}
      onClick={(e) => { e.stopPropagation(); onToggle(hunkKey); }}
    >
      <input
        type="checkbox"
        checked={isSelected}
        onChange={() => onToggle(hunkKey)}
        className="accent-accent cursor-pointer w-3.5 h-3.5 flex-shrink-0"
        onClick={(e) => e.stopPropagation()}
      />
      <span className={`font-mono text-[0.75rem] cursor-pointer ${
        isSelected ? 'text-accent font-medium' : 'text-text-secondary'
      }`}>
        {isSelected ? 'selected' : 'select hunk'}
      </span>
      {hunkContext && (
        <span className="font-mono text-[0.7rem] text-text-muted ml-auto truncate max-w-[60%]" title={hunkContext}>
          {hunkContext}
        </span>
      )}
    </div>
  );
}

function AcceptBanner({
  count,
  onAcceptInto,
  onClear,
  onSplit,
  actionLabel = 'Accept into...',
}: {
  count: number;
  onAcceptInto: () => void;
  onClear: () => void;
  onSplit?: () => void;
  actionLabel?: string;
}) {
  return (
    <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 px-4 py-2.5 bg-surface border border-accent/30 rounded-lg shadow-lg">
      <span className="font-mono text-[0.75rem] text-text-primary">
        {count} hunk{count === 1 ? '' : 's'} selected
      </span>
      <button
        type="button"
        className="appearance-none border border-accent bg-accent/10 text-accent text-[0.72rem] font-mono px-3 py-1 rounded-md cursor-pointer hover:bg-accent/20 transition-colors duration-100"
        onClick={onAcceptInto}
      >
        {actionLabel}
      </button>
      {onSplit && (
        <button
          type="button"
          className="appearance-none border border-green-500/40 bg-green-500/10 text-green-400 text-[0.72rem] font-mono px-3 py-1 rounded-md cursor-pointer hover:bg-green-500/20 transition-colors duration-100"
          onClick={onSplit}
        >
          Split
        </button>
      )}
      <button
        type="button"
        className="appearance-none border border-border bg-transparent text-text-secondary text-[0.72rem] font-mono px-2.5 py-1 rounded-md cursor-pointer hover:bg-surface-hover hover:text-text-primary transition-colors duration-100"
        onClick={onClear}
      >
        Clear
      </button>
    </div>
  );
}

function BranchPicker({
  branches,
  currentBranch,
  sourceBranch,
  onSelect,
  onCancel,
  isMoving,
  moveError,
  mode = 'move',
  suggestedBranch,
}: {
  branches: StackBranch[];
  currentBranch: string;
  sourceBranch: string;
  onSelect: (branch: string) => void;
  onCancel: () => void;
  isMoving: boolean;
  moveError: string | null;
  mode?: 'move' | 'accept';
  suggestedBranch?: string | null;
}) {
  const parentBranch = branches.find((b) => b.name === sourceBranch)?.parent;
  const headerText = mode === 'accept' ? 'Accept into branch' : 'Move to branch';
  const loadingText = mode === 'accept' ? 'Accepting lines...' : 'Moving lines...';

  return (
    <div
      className="flex flex-col gap-1 p-2.5 my-1 bg-surface-hover border border-border rounded-md"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="font-mono text-[0.72rem] font-semibold text-text-primary">
          {headerText}
        </span>
        <button
          type="button"
          className="appearance-none border-none bg-transparent text-text-muted text-[0.65rem] cursor-pointer hover:text-text-primary p-0.5"
          onClick={onCancel}
          disabled={isMoving}
        >
          Cancel
        </button>
      </div>
      {isMoving && (
        <p className="text-[0.72rem] text-text-muted font-mono m-0">{loadingText}</p>
      )}
      {moveError && (
        <p className="text-[0.72rem] text-danger font-mono m-0">{moveError}</p>
      )}
      {!isMoving && (
        <div className="flex flex-col gap-0.5 max-h-[200px] overflow-y-auto">
          {branches
            .filter((b) => b.name !== sourceBranch)
            .map((branch) => {
              const isParent = branch.name === parentBranch;
              const isSuggested = suggestedBranch != null && branch.name === suggestedBranch;
              return (
              <button
                key={branch.name}
                type="button"
                className={`appearance-none border-none rounded-md py-1.5 px-2 text-left text-[0.75rem] font-mono cursor-pointer transition-colors duration-100 ${
                  isSuggested
                    ? 'bg-green-500/10 text-green-400 hover:bg-green-500/20'
                    : isParent
                    ? 'bg-accent/10 text-accent hover:bg-accent/20'
                    : 'bg-transparent text-text-secondary hover:bg-surface-active hover:text-text-primary'
                }`}
                style={{ paddingInlineStart: `${8 + branch.depth * 10}px` }}
                onClick={() => onSelect(branch.name)}
              >
                {branch.name}
                {isSuggested && (
                  <span className="ml-1.5 text-[0.6rem] text-green-400/70">(suggested)</span>
                )}
                {isParent && !isSuggested && (
                  <span className="ml-1.5 text-[0.6rem] text-text-muted">(parent)</span>
                )}
              </button>
              );
            })}
        </div>
      )}
    </div>
  );
}

function SplitDialog({
  onSubmit,
  onCancel,
  isSplitting,
  splitError,
}: {
  onSubmit: (commitMessage: string, placement: 'before' | 'after') => void;
  onCancel: () => void;
  isSplitting: boolean;
  splitError: string | null;
}) {
  const [commitMessage, setCommitMessage] = useState('');
  const [placement, setPlacement] = useState<'before' | 'after'>('before');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && commitMessage.trim()) {
      e.preventDefault();
      onSubmit(commitMessage.trim(), placement);
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
          Split into new branch
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
          <div className="flex gap-3">
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input
                type="radio"
                name="split-placement"
                checked={placement === 'before'}
                onChange={() => setPlacement('before')}
                className="accent-accent cursor-pointer"
              />
              <span className="font-mono text-[0.72rem] text-text-secondary">Before (parent)</span>
            </label>
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input
                type="radio"
                name="split-placement"
                checked={placement === 'after'}
                onChange={() => setPlacement('after')}
                className="accent-accent cursor-pointer"
              />
              <span className="font-mono text-[0.72rem] text-text-secondary">After (child)</span>
            </label>
          </div>
          <button
            type="button"
            className="appearance-none border border-accent bg-accent/10 text-accent text-[0.72rem] font-mono px-3 py-1.5 rounded-md cursor-pointer hover:bg-accent/20 transition-colors duration-100 disabled:opacity-40 disabled:cursor-not-allowed self-end"
            disabled={!commitMessage.trim()}
            onClick={() => { if (commitMessage.trim()) onSubmit(commitMessage.trim(), placement); }}
          >
            Split
          </button>
        </>
      )}
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

const EMPTY_HUNKS: ParsedHunk[] = [];
const EMPTY_COMMENTS: DiffComment[] = [];
const EMPTY_HUNK_KEYS: Set<HunkKey> = new Set();
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
  fileSelectedHunks,
  onHunkToggle,
  fileParsedHunks,
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
  fileSelectedHunks: Set<HunkKey>;
  onHunkToggle: (key: HunkKey) => void;
  fileParsedHunks: ParsedHunk[];
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

  const options = useMemo<PatchDiffProps<CommentMeta>['options']>(
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
      unsafeCSS: `
        [data-diffs-header] { position: sticky; top: 0; z-index: 10; }
        [data-selected-line] { background: rgba(250, 204, 21, 0.10) !important; }
      `,
    }),
    [diffStyle, handleSelectionEnd, rt, activeTheme],
  );

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

    // Add hunk toggle annotations for both working changes and branch diffs
    for (const hunk of fileParsedHunks) {
      const key: HunkKey = `${hunk.file}:${hunk.hunkIndex}`;
      annotations.push({
        lineNumber: hunk.firstChangedLine - 1,
        side: hunk.side,
        metadata: {
          commentId: `__hunktoggle__${key}`,
          text: '',
          isInput: false,
          isHunkToggle: true,
          hunkKey: key,
          isSelected: fileSelectedHunks.has(key),
          hunkContext: hunk.hunkContext,
        },
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
  }, [fileComments, activeInput, editingComment, toolbarState, fileParsedHunks, fileSelectedHunks, fileSplitSelections, fileInfo.path]);

  const renderAnnotation = useCallback(
    (annotation: DiffLineAnnotation<CommentMeta>) => {
      const { metadata } = annotation;

      if (metadata.isHunkToggle && metadata.hunkKey) {
        return (
          <HunkToggle
            hunkKey={metadata.hunkKey}
            isSelected={metadata.isSelected ?? false}
            onToggle={onHunkToggle}
            hunkContext={metadata.hunkContext ?? null}
          />
        );
      }

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

      return (
        <SavedComment
          comment={comment}
          onEdit={() => onStartEdit(comment)}
          onDelete={onDeleteComment}
        />
      );
    },
    [fileComments, editingComment, activeInput, toolbarState, fileInfo.path, onAddComment, onUpdateComment, onDeleteComment, onCancelInput, onStartEdit, onToolbarComment, onToolbarSplit, onHunkToggle, fileSplitSelections, onDeleteSplitSelection],
  );

  const renderHeaderMetadata = useCallback(() => {
    if (!onToggleViewed) return null;
    return (
      <button
        type="button"
        className="appearance-none border border-border bg-transparent text-text-muted text-[0.65rem] font-mono px-2 py-0.5 rounded cursor-pointer hover:bg-surface-hover hover:text-text-primary hover:border-border-strong transition-colors duration-100 whitespace-nowrap"
        onClick={(e) => { e.stopPropagation(); onToggleViewed(fileInfo.path); }}
      >
        Viewed
      </button>
    );
  }, [onToggleViewed, fileInfo.path]);

  return (
    <PatchDiff<CommentMeta>
      patch={patch}
      options={options}
      lineAnnotations={lineAnnotations}
      renderAnnotation={renderAnnotation}
      selectedLines={selectedLines}
      renderHeaderMetadata={onToggleViewed ? renderHeaderMetadata : undefined}
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

export default function App() {
  const isWideScreen = useMediaQuery('(min-width: 961px)');
  const savedLayout = useDefaultLayout({
    id: 'stack-explorer-layout',
    storage: localStorage,
    panelIds: ['sidebar', 'content'],
  });
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
  const [collapsedDirs, setCollapsedDirs] = useState<Set<string>>(new Set());
  const [fileFilter, setFileFilter] = useState('');
  const [activeFileIndex, setActiveFileIndex] = useState<number | null>(null);
  const fileRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const [comments, setComments] = useState<DiffComment[]>([]);
  const [activeInput, setActiveInput] = useState<ActiveInput>(null);
  const [editingComment, setEditingComment] = useState<DiffComment | null>(null);
  const [copyFeedback, setCopyFeedback] = useState(false);
  const [toolbarState, setToolbarState] = useState<ActiveInput>(null);
  const [isMoving, setIsMoving] = useState(false);
  const [moveError, setMoveError] = useState<string | null>(null);
  const [moveSuccess, setMoveSuccess] = useState<string | null>(null);
  const [selectedHunks, setSelectedHunks] = useState<Set<HunkKey>>(new Set());
  const [showAcceptPicker, setShowAcceptPicker] = useState(false);
  const [isAccepting, setIsAccepting] = useState(false);
  const [acceptError, setAcceptError] = useState<string | null>(null);
  const [showMovePicker, setShowMovePicker] = useState(false);
  const [showSplitDialog, setShowSplitDialog] = useState(false);
  const [isSplitting, setIsSplitting] = useState(false);
  const [splitError, setSplitError] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<HunkSuggestionItem[]>([]);
  const [splitLineSelections, setSplitLineSelections] = useState<SplitLineSelection[]>([]);
  const [showSplitLinesDialog, setShowSplitLinesDialog] = useState(false);
  const [isSplittingLines, setIsSplittingLines] = useState(false);
  const [splitLinesError, setSplitLinesError] = useState<string | null>(null);
  const [viewedFiles, setViewedFiles] = useState<Set<string>>(new Set());
  const [expandedLargeFiles, setExpandedLargeFiles] = useState<Set<string>>(new Set());
  const [githubInfo, setGithubInfo] = useState<Record<string, GitHubBranchInfo>>({});
  const [isGithubInfoLoading, setIsGithubInfoLoading] = useState(true);

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
    setCollapsedDirs(new Set());
    setFileFilter('');
    setActiveFileIndex(null);
    fileRefs.current = {};
    setComments([]);
    setActiveInput(null);
    setEditingComment(null);
    setToolbarState(null);
    setMoveError(null);
    setMoveSuccess(null);
    setSelectedHunks(new Set());
    setShowAcceptPicker(false);
    setAcceptError(null);
    setShowMovePicker(false);
    setShowSplitDialog(false);
    setSplitError(null);
    setSplitLineSelections([]);
    setShowSplitLinesDialog(false);
    setSplitLinesError(null);
    setViewedFiles(new Set());
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

  const fileTree = useMemo(
    () => buildFileTree(fileInfos),
    [fileInfos],
  );

  const parsedHunks = useMemo(() => {
    if (!selection) return [];
    return diffPatches.flatMap((patch, i) => {
      const info = fileInfos[i];
      if (!info) return [];
      return parseHunksFromPatch(patch, info, i);
    });
  }, [diffPatches, fileInfos, selection]);

  const parsedHunksByFile = useMemo(() => {
    const map = new Map<string, ParsedHunk[]>();
    for (const hunk of parsedHunks) {
      let arr = map.get(hunk.file);
      if (!arr) { arr = []; map.set(hunk.file, arr); }
      arr.push(hunk);
    }
    return map;
  }, [parsedHunks]);

  const deferredSelectedHunks = useDeferredValue(selectedHunks);

  const prevSelectedByFileRef = useRef(new Map<string, Set<HunkKey>>());
  const selectedHunksByFile = useMemo(() => {
    const prev = prevSelectedByFileRef.current;
    const next = new Map<string, Set<HunkKey>>();
    for (const key of deferredSelectedHunks) {
      const file = key.split(':')[0]!;
      let s = next.get(file);
      if (!s) { s = new Set(); next.set(file, s); }
      s.add(key);
    }
    // Reuse previous references for unchanged files to preserve React.memo
    const result = new Map<string, Set<HunkKey>>();
    const allFiles = new Set([...prev.keys(), ...next.keys()]);
    for (const file of allFiles) {
      const prevSet = prev.get(file);
      const nextSet = next.get(file);
      if (prevSet && nextSet && prevSet.size === nextSet.size) {
        let same = true;
        for (const k of prevSet) { if (!nextSet.has(k)) { same = false; break; } }
        if (same) { result.set(file, prevSet); continue; }
      }
      if (nextSet) result.set(file, nextSet);
    }
    prevSelectedByFileRef.current = result;
    return result;
  }, [deferredSelectedHunks]);

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

  const toggleDir = useCallback((path: string) => {
    setCollapsedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const toggleViewed = useCallback((path: string) => {
    setViewedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
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
    requestAnimationFrame(() => {
      fileRefs.current[index]?.scrollIntoView({ behavior: 'instant', block: 'start' });
    });
  }, [fileInfos, viewedFiles]);

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

  const [, startHunkTransition] = useTransition();

  const handleHunkToggle = useCallback((key: HunkKey) => {
    startHunkTransition(() => {
      setSelectedHunks((prev) => {
        const next = new Set(prev);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return next;
      });
    });
  }, [startHunkTransition]);

  const refreshData = useCallback(async () => {
    try {
      const stackData = await fetchJSON<StackResponse>('/api/stack');
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

  // Poll for external changes every 3 seconds
  const lastStackKeyRef = useRef<string>('');
  const refreshDataRef = useRef(refreshData);
  refreshDataRef.current = refreshData;

  useEffect(() => {
    if (stack) {
      lastStackKeyRef.current = JSON.stringify(
        stack.branches.map((b) => ({ name: b.name, commit: b.commit })),
      );
    }
  }, [stack]);

  useEffect(() => {
    let intervalId: ReturnType<typeof setInterval> | null = null;

    const startPolling = () => {
      if (intervalId) return;
      intervalId = setInterval(async () => {
        if (isMoving || isAccepting || isSplitting || isSplittingLines) return;
        try {
          const data = await fetchJSON<StackResponse>('/api/stack');
          const newKey = JSON.stringify(
            data.branches.map((b) => ({ name: b.name, commit: b.commit })),
          );
          if (newKey !== lastStackKeyRef.current) {
            await refreshDataRef.current();
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
  }, [isMoving, isAccepting, isSplitting, isSplittingLines]);

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

  const handleMoveHunksBranchSelect = useCallback(
    async (targetBranch: string) => {
      if (selectedHunks.size === 0 || selection?.type !== 'branch') return;

      setIsMoving(true);
      setMoveError(null);

      const hunkPayload = [...selectedHunks].map((key) => {
        const [filePath, hunkIndexStr] = key.split(':') as [string, string];
        const hunkIndex = parseInt(hunkIndexStr, 10);
        const parsed = parsedHunks.find(
          (h) => h.file === filePath && h.hunkIndex === hunkIndex,
        );
        const patchIdx = parsed?.patchIndex ?? 0;
        return {
          filePath,
          filePatch: diffPatches[patchIdx] ?? '',
          hunkIndex,
        };
      });

      try {
        await postJSON<MoveHunksResponse>('/api/move-hunks', {
          sourceBranch: selection.name,
          targetBranch,
          hunks: hunkPayload,
        });

        setSelectedHunks(new Set());
        setShowMovePicker(false);
        setMoveSuccess(`Moved to ${targetBranch}`);
        setTimeout(() => setMoveSuccess(null), 3000);

        await refreshData();
      } catch (err) {
        setMoveError(err instanceof Error ? err.message : 'Move failed');
      } finally {
        setIsMoving(false);
      }
    },
    [selectedHunks, selection, diffPatches, parsedHunks, refreshData],
  );

  const handleMoveCancelPicker = useCallback(() => {
    setShowMovePicker(false);
    setMoveError(null);
  }, []);

  const handleSplitSubmit = useCallback(
    async (commitMessage: string, placement: 'before' | 'after') => {
      if (selectedHunks.size === 0 || selection?.type !== 'branch') return;

      setIsSplitting(true);
      setSplitError(null);

      const hunkPayload = [...selectedHunks].map((key) => {
        const [filePath, hunkIndexStr] = key.split(':') as [string, string];
        const hunkIndex = parseInt(hunkIndexStr, 10);
        const parsed = parsedHunks.find(
          (h) => h.file === filePath && h.hunkIndex === hunkIndex,
        );
        const patchIdx = parsed?.patchIndex ?? 0;
        return {
          filePath,
          filePatch: diffPatches[patchIdx] ?? '',
          hunkIndex,
        };
      });

      try {
        const result = await postJSON<SplitHunksResponse>('/api/split-hunks', {
          sourceBranch: selection.name,
          commitMessage,
          placement,
          hunks: hunkPayload,
        });

        setSelectedHunks(new Set());
        setShowSplitDialog(false);
        setMoveSuccess(`Split into ${result.newBranch}`);
        setTimeout(() => setMoveSuccess(null), 3000);

        await refreshData();
      } catch (err) {
        setSplitError(err instanceof Error ? err.message : 'Split failed');
      } finally {
        setIsSplitting(false);
      }
    },
    [selectedHunks, selection, diffPatches, parsedHunks, refreshData],
  );

  const handleSplitCancel = useCallback(() => {
    setShowSplitDialog(false);
    setSplitError(null);
  }, []);

  const handleAcceptBranchSelect = useCallback(
    async (targetBranch: string) => {
      if (selectedHunks.size === 0 || selection?.type !== 'working') return;

      setIsAccepting(true);
      setAcceptError(null);

      // Build hunk selections from selectedHunks set
      const hunkPayload = [...selectedHunks].map((key) => {
        const [filePath, hunkIndexStr] = key.split(':') as [string, string];
        const hunkIndex = parseInt(hunkIndexStr, 10);
        // Find the matching parsed hunk to get the patchIndex
        const parsed = parsedHunks.find(
          (h) => h.file === filePath && h.hunkIndex === hunkIndex,
        );
        const patchIdx = parsed?.patchIndex ?? 0;
        return {
          filePath,
          filePatch: diffPatches[patchIdx] ?? '',
          hunkIndex,
        };
      });

      try {
        await postJSON<AcceptHunksResponse>('/api/accept-working-hunks', {
          targetBranch,
          hunks: hunkPayload,
        });

        setSelectedHunks(new Set());
        setShowAcceptPicker(false);
        setMoveSuccess(`Accepted into ${targetBranch}`);
        setTimeout(() => setMoveSuccess(null), 3000);

        await refreshData();
      } catch (err) {
        setAcceptError(err instanceof Error ? err.message : 'Accept failed');
      } finally {
        setIsAccepting(false);
      }
    },
    [selectedHunks, selection, diffPatches, parsedHunks, refreshData],
  );

  const handleAcceptCancelPicker = useCallback(() => {
    setShowAcceptPicker(false);
    setAcceptError(null);
  }, []);

  const handleCopyComments = useCallback(() => {
    if (comments.length === 0) return;
    const markdown = comments
      .map((c) => `- \`${formatLineRef(c.file, c.startLine, c.endLine)}\` - ${c.text}`)
      .join('\n');
    void navigator.clipboard.writeText(markdown).then(() => {
      setCopyFeedback(true);
      setTimeout(() => setCopyFeedback(false), 2000);
    });
  }, [comments]);

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

  const suggestedBranch = useMemo(() => {
    if (suggestions.length === 0 || selectedHunks.size === 0) return null;
    const votes = new Map<string, number>();
    for (const key of selectedHunks) {
      const [filePath, hunkIndexStr] = key.split(':') as [string, string];
      const hunkIndex = parseInt(hunkIndexStr, 10);
      const match = suggestions.find(
        (s) => s.file === filePath && s.hunkIndex === hunkIndex && s.suggestedBranch,
      );
      if (match?.suggestedBranch) {
        votes.set(match.suggestedBranch, (votes.get(match.suggestedBranch) ?? 0) + 1);
      }
    }
    if (votes.size === 0) return null;
    const sorted = [...votes.entries()].sort((a, b) => b[1] - a[1]);
    // No suggestion on tie
    if (sorted.length > 1 && sorted[0]![1] === sorted[1]![1]) return null;
    return sorted[0]![0] ?? null;
  }, [suggestions, selectedHunks]);

  return (
    <WorkerPoolContextProvider
      poolOptions={{ workerFactory: () => new DiffsWorker(), poolSize: 4 }}
      highlighterOptions={{}}
    >
    <main className={`relative h-screen animate-fade-in overflow-hidden ${isWideScreen ? '' : 'flex flex-col'}`}>
      {isWideScreen ? (
      <Group orientation="horizontal" {...savedLayout}>
      <Panel id="sidebar" defaultSize="20%" minSize="15%" maxSize="40%">
      <section className="border-r border-border bg-surface overflow-hidden flex flex-col h-full">
        <div className="px-[1.15rem] h-[60px] shrink-0 flex flex-col justify-center border-b border-border">
          <p className="font-mono text-[0.68rem] font-medium uppercase tracking-[0.13em] text-accent m-0 mb-[0.3rem]">
            Shortcake
          </p>
          <h1>Stack Diff Explorer</h1>
        </div>

        {isStackLoading ? (
          <p className="m-[1.15rem] text-text-muted text-[0.88rem]">Loading stack…</p>
        ) : null}

        {!isStackLoading && branches.length === 0 ? (
          <p className="m-[1.15rem] text-text-muted text-[0.88rem]">
            No tracked branches found in this repository.
          </p>
        ) : null}

        <div
          className="relative flex flex-col gap-0 p-1.5 overflow-y-auto overflow-x-clip flex-1"
          role="list"
          aria-label="Tracked stack branches"
        >
          <button
            className={`relative appearance-none rounded-md py-[5px] px-[7px] mx-[8px] mb-1 text-left text-text-primary cursor-pointer transition-[background] duration-150 ease-in-out border-none ${selection?.type === 'working' ? 'bg-accent-bg' : 'bg-transparent hover:bg-surface-hover'}`}
            onClick={() => setSelection({ type: 'working' })}
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

          {branches.length > 0 && (
            <div className="border-t border-border mx-2 my-1" />
          )}

          {branches.map((branch, index) => {
            const active = selection?.type === 'branch' && branch.name === selection.name;
            const branchPadding =
              STACK_CARD_INDENT_BASE + branch.depth * STACK_CARD_INDENT_STEP;
            const parentIndex = parentIndexMap.get(branch.parent) ?? -1;
            const lastChildIdx = lastChildIndexMap.get(index);
            const ghInfo = githubInfo[branch.name];

            return (
              <React.Fragment key={branch.name}>
                <button
                  className={`relative appearance-none rounded-md py-[5px] px-[7px] text-left text-text-primary cursor-pointer transition-[background] duration-150 ease-in-out border-none ${active ? 'bg-accent-bg' : 'bg-transparent hover:bg-surface-hover'}`}
                  style={{
                    anchorName: `--branch-${index}`,
                    marginInlineStart: `${branchPadding}px`,
                    marginInlineEnd: '8px',
                  } as React.CSSProperties}
                  onClick={() => setSelection({ type: 'branch', name: branch.name })}
                  type="button"
                >
                  <span className="relative z-[2] flex items-center gap-[7px] w-full">
                    <span className="text-[0.88rem] font-semibold whitespace-nowrap overflow-hidden text-ellipsis">
                      {branch.name}
                    </span>
                    {branch.isCurrent && (
                      <span className="font-mono text-[0.58rem] font-medium uppercase tracking-[0.05em] text-accent bg-accent/10 border border-accent/18 ml-1.5 px-[5px] py-px rounded-full shrink-0 leading-[1.5]">
                        current
                      </span>
                    )}
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
      </section>
      </Panel>
      <Separator className="resize-handle" />
      <Panel id="content" minSize="40%">
      <section className="bg-surface overflow-hidden flex flex-col min-w-0 h-full">
        <SettingsModal
          isOpen={showSettings}
          onClose={() => setShowSettings(false)}
          diffThemeDark={diffThemeDark}
          diffThemeLight={diffThemeLight}
          onDarkChange={setDiffThemeDark}
          onLightChange={setDiffThemeLight}
        />
        <header className="px-[1.15rem] h-[60px] shrink-0 border-b border-border flex justify-between items-center gap-4">
          <div>
            <p className="font-mono text-[0.68rem] font-medium uppercase tracking-[0.13em] text-accent m-0 mb-[0.3rem]">
              Diff
            </p>
            <h2>
              {selection?.type === 'working' ? (
                'Uncommitted changes'
              ) : diff ? (
                <>
                  {diff.branch}{' '}
                  <span className="text-text-muted font-normal">&rarr;</span>{' '}
                  {diff.parent}
                </>
              ) : (
                'Select a branch'
              )}
            </h2>
          </div>

          <div className="flex items-center gap-2">
            {moveSuccess && (
              <span className="font-mono text-[0.7rem] text-accent whitespace-nowrap">
                {moveSuccess}
              </span>
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
            <aside className="w-[260px] min-w-[260px] border-r border-border flex flex-col overflow-hidden max-[1100px]:hidden">
              <div className="px-3.5 py-2.5 text-[0.8rem] font-semibold text-text-secondary border-b border-border">
                Files changed ({fileInfos.length})
              </div>
              <div className="px-2.5 py-2 border-b border-border">
                <input
                  className="w-full appearance-none border border-border rounded-md bg-surface-hover text-text-primary font-mono text-[0.72rem] px-[9px] py-1.5 outline-none transition-[border-color] duration-150 ease-in-out focus:border-border-strong placeholder:text-text-muted"
                  type="text"
                  placeholder="Filter files..."
                  value={fileFilter}
                  onChange={(e) => setFileFilter(e.target.value)}
                />
              </div>
              <div className="flex-1 overflow-y-auto py-1">
                <FileTreeEntries
                  entries={fileTree}
                  depth={0}
                  collapsedDirs={collapsedDirs}
                  onToggleDir={toggleDir}
                  activeIndex={activeFileIndex}
                  onFileClick={scrollToFile}
                  filter={fileFilter}
                  viewedFiles={viewedFiles}
                />
              </div>
            </aside>

            <div className="diff-content flex-1 min-w-0 overflow-auto">
              {diffPatches.map((patch, index) => {
                const info = fileInfos[index];
                if (!info) return null;
                const isViewed = viewedFiles.has(info.path);
                return (
                  <div
                    className={index > 0 ? 'border-t border-border' : undefined}
                    key={`${selection?.type === 'working' ? 'working' : diff?.branch}-${index}`}
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
                          fileSelectedHunks={selectedHunksByFile.get(info.path) ?? EMPTY_HUNK_KEYS}
                          onHunkToggle={handleHunkToggle}
                          fileParsedHunks={parsedHunksByFile.get(info.path) ?? EMPTY_HUNKS}
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
      </Panel>
      </Group>
      ) : (
      <>
      <section className="border-b border-border bg-surface overflow-hidden flex flex-col max-h-[280px]">
        <div className="px-[1.15rem] h-[60px] shrink-0 flex flex-col justify-center border-b border-border">
          <p className="font-mono text-[0.68rem] font-medium uppercase tracking-[0.13em] text-accent m-0 mb-[0.3rem]">
            Shortcake
          </p>
          <h1>Stack Diff Explorer</h1>
        </div>

        {isStackLoading ? (
          <p className="m-[1.15rem] text-text-muted text-[0.88rem]">Loading stack…</p>
        ) : null}

        {!isStackLoading && branches.length === 0 ? (
          <p className="m-[1.15rem] text-text-muted text-[0.88rem]">
            No tracked branches found in this repository.
          </p>
        ) : null}

        <div
          className="relative flex flex-col gap-0 p-1.5 overflow-y-auto overflow-x-clip flex-1"
          role="list"
          aria-label="Tracked stack branches"
        >
          <button
            className={`relative appearance-none rounded-md py-[5px] px-[7px] mx-[8px] mb-1 text-left text-text-primary cursor-pointer transition-[background] duration-150 ease-in-out border-none ${selection?.type === 'working' ? 'bg-accent-bg' : 'bg-transparent hover:bg-surface-hover'}`}
            onClick={() => setSelection({ type: 'working' })}
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

          {branches.length > 0 && (
            <div className="border-t border-border mx-2 my-1" />
          )}

          {branches.map((branch) => {
            const active = selection?.type === 'branch' && branch.name === selection.name;
            const branchPadding =
              STACK_CARD_INDENT_BASE + branch.depth * STACK_CARD_INDENT_STEP;
            const ghInfo = githubInfo[branch.name];

            return (
              <button
                key={branch.name}
                className={`relative appearance-none rounded-md py-[5px] px-[7px] text-left text-text-primary cursor-pointer transition-[background] duration-150 ease-in-out border-none ${active ? 'bg-accent-bg' : 'bg-transparent hover:bg-surface-hover'}`}
                style={{
                  marginInlineStart: `${branchPadding}px`,
                  marginInlineEnd: '8px',
                } as React.CSSProperties}
                onClick={() => setSelection({ type: 'branch', name: branch.name })}
                type="button"
              >
                <span className="relative z-[2] flex items-center gap-[7px] w-full">
                  <span className="text-[0.88rem] font-semibold whitespace-nowrap overflow-hidden text-ellipsis">
                    {branch.name}
                  </span>
                  {branch.isCurrent && (
                    <span className="font-mono text-[0.58rem] font-medium uppercase tracking-[0.05em] text-accent bg-accent/10 border border-accent/18 ml-1.5 px-[5px] py-px rounded-full shrink-0 leading-[1.5]">
                      current
                    </span>
                  )}
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
          })}
        </div>
      </section>

      <section className="bg-surface overflow-hidden flex flex-col min-w-0 flex-1">
        <header className="px-[1.15rem] h-[60px] shrink-0 border-b border-border flex justify-between items-center gap-4">
          <div>
            <p className="font-mono text-[0.68rem] font-medium uppercase tracking-[0.13em] text-accent m-0 mb-[0.3rem]">
              Diff
            </p>
            <h2>
              {selection?.type === 'working' ? (
                'Uncommitted changes'
              ) : diff ? (
                <>
                  {diff.branch}{' '}
                  <span className="text-text-muted font-normal">&rarr;</span>{' '}
                  {diff.parent}
                </>
              ) : (
                'Select a branch'
              )}
            </h2>
          </div>

          <div className="flex items-center gap-2">
            {moveSuccess && (
              <span className="font-mono text-[0.7rem] text-accent whitespace-nowrap">
                {moveSuccess}
              </span>
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
          <div className="diff-content flex-1 min-w-0 min-h-0 overflow-auto">
            {diffPatches.map((patch, index) => {
              const info = fileInfos[index];
              if (!info) return null;
              const isViewed = viewedFiles.has(info.path);
              return (
                <div
                  className={index > 0 ? 'border-t border-border' : undefined}
                  key={`mobile-${selection?.type === 'working' ? 'working' : diff?.branch}-${index}`}
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
                        fileSelectedHunks={selectedHunksByFile.get(info.path) ?? EMPTY_HUNK_KEYS}
                        onHunkToggle={handleHunkToggle}
                        fileParsedHunks={parsedHunksByFile.get(info.path) ?? EMPTY_HUNKS}
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
        )}
      </section>
      </>
      )}

      {selection?.type === 'working' && selectedHunks.size > 0 && !showAcceptPicker && (
        <AcceptBanner
          count={selectedHunks.size}
          onAcceptInto={() => {
            setShowAcceptPicker(true);
            setAcceptError(null);
            setSuggestions([]);
            fetchJSON<SuggestionsResponse>('/api/suggestions?mode=working')
              .then((res) => setSuggestions(res.suggestions))
              .catch(() => setSuggestions([]));
          }}
          onClear={() => setSelectedHunks(new Set())}
        />
      )}

      {selection?.type === 'branch' && selectedHunks.size > 0 && !showMovePicker && !showSplitDialog && (
        <AcceptBanner
          count={selectedHunks.size}
          onAcceptInto={() => {
            setShowMovePicker(true);
            setMoveError(null);
            setSuggestions([]);
            fetchJSON<SuggestionsResponse>(`/api/suggestions?mode=branch&source=${encodeURIComponent(selection.name)}`)
              .then((res) => setSuggestions(res.suggestions))
              .catch(() => setSuggestions([]));
          }}
          onClear={() => setSelectedHunks(new Set())}
          actionLabel="Move to..."
          onSplit={() => {
            setShowSplitDialog(true);
            setSplitError(null);
          }}
        />
      )}

      {showAcceptPicker && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 w-[320px]">
          <BranchPicker
            branches={branches}
            currentBranch=""
            sourceBranch=""
            onSelect={handleAcceptBranchSelect}
            onCancel={handleAcceptCancelPicker}
            isMoving={isAccepting}
            moveError={acceptError}
            mode="accept"
            suggestedBranch={suggestedBranch}
          />
        </div>
      )}

      {showMovePicker && selection?.type === 'branch' && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 w-[320px]">
          <BranchPicker
            branches={branches}
            currentBranch={selection.name}
            sourceBranch={selection.name}
            onSelect={handleMoveHunksBranchSelect}
            onCancel={handleMoveCancelPicker}
            isMoving={isMoving}
            moveError={moveError}
            mode="move"
            suggestedBranch={suggestedBranch}
          />
        </div>
      )}

      {showSplitDialog && selection?.type === 'branch' && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 w-[360px]">
          <SplitDialog
            onSubmit={handleSplitSubmit}
            onCancel={handleSplitCancel}
            isSplitting={isSplitting}
            splitError={splitError}
          />
        </div>
      )}

      {selection?.type === 'branch' && splitLineSelections.length > 0 && selectedHunks.size === 0 && !showSplitLinesDialog && (
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
    </main>
    </WorkerPoolContextProvider>
  );
}
