import {
  PatchDiff,
  type PatchDiffProps,
  type DiffLineAnnotation,
  type AnnotationSide,
  type SelectedLineRange,
} from '@pierre/diffs/react';
import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
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

type DiffSelection =
  | { type: 'branch'; name: string }
  | { type: 'working' };

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
}: {
  lineLabel: string;
  onComment: () => void;
}) {
  return (
    <div
      className="flex items-center gap-2 p-2 my-1 bg-surface-hover border border-border rounded-md"
      onClick={(e) => e.stopPropagation()}
    >
      <span className="font-mono text-[0.65rem] text-text-muted mr-auto">{lineLabel}</span>
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
  actionLabel = 'Accept into...',
}: {
  count: number;
  onAcceptInto: () => void;
  onClear: () => void;
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
}: {
  branches: StackBranch[];
  currentBranch: string;
  sourceBranch: string;
  onSelect: (branch: string) => void;
  onCancel: () => void;
  isMoving: boolean;
  moveError: string | null;
  mode?: 'move' | 'accept';
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
            .map((branch) => (
              <button
                key={branch.name}
                type="button"
                className={`appearance-none border-none rounded-md py-1.5 px-2 text-left text-[0.75rem] font-mono cursor-pointer transition-colors duration-100 ${
                  branch.name === parentBranch
                    ? 'bg-accent/10 text-accent hover:bg-accent/20'
                    : 'bg-transparent text-text-secondary hover:bg-surface-active hover:text-text-primary'
                }`}
                style={{ paddingInlineStart: `${8 + branch.depth * 10}px` }}
                onClick={() => onSelect(branch.name)}
              >
                {branch.name}
                {branch.name === parentBranch && (
                  <span className="ml-1.5 text-[0.6rem] text-text-muted">(parent)</span>
                )}
              </button>
            ))}
        </div>
      )}
    </div>
  );
}

function DiffFileSection({
  patch,
  fileInfo,
  comments,
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
  selectedHunks,
  onHunkToggle,
  parsedHunks,
  diffStyle,
  resolvedTheme,
  onToggleViewed,
}: {
  patch: string;
  fileInfo: FileInfo;
  comments: DiffComment[];
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
  selectedHunks: Set<HunkKey>;
  onHunkToggle: (key: HunkKey) => void;
  parsedHunks: ParsedHunk[];
  diffStyle: DiffStyle;
  resolvedTheme?: 'dark' | 'light';
  onToggleViewed?: (path: string) => void;
}) {
  const fileComments = comments.filter((c) => c.file === fileInfo.path);

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

  const options = useMemo<PatchDiffProps<CommentMeta>['options']>(
    () => ({
      diffStyle,
      diffIndicators: 'classic',
      hunkSeparators: 'metadata',
      theme: rt === 'light' ? 'pierre-light' : 'pierre-dark',
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
    [diffStyle, handleSelectionEnd, rt],
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
    for (const hunk of parsedHunks) {
      if (hunk.file !== fileInfo.path) continue;
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
          isSelected: selectedHunks.has(key),
          hunkContext: hunk.hunkContext,
        },
      });
    }

    return annotations;
  }, [fileComments, activeInput, editingComment, toolbarState, parsedHunks, selectedHunks, fileInfo.path]);

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

      if (metadata.isToolbar && toolbarState) {
        return (
          <SelectionToolbar
            lineLabel={formatLineLabel(toolbarState.startLine, toolbarState.endLine)}
            onComment={onToolbarComment}
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
    [fileComments, editingComment, activeInput, toolbarState, fileInfo.path, onAddComment, onUpdateComment, onDeleteComment, onCancelInput, onStartEdit, onToolbarComment, onHunkToggle],
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
}

export default function App() {
  const isWideScreen = useMediaQuery('(min-width: 961px)');
  const savedLayout = useDefaultLayout({
    id: 'stack-explorer-layout',
    storage: localStorage,
    panelIds: ['sidebar', 'content'],
  });
  const [stack, setStack] = useState<StackResponse | null>(null);
  const [selection, setSelection] = useState<DiffSelection | null>(null);
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
  const [viewedFiles, setViewedFiles] = useState<Set<string>>(new Set());

  const [themeMode, setThemeMode] = useState<ThemeMode>(
    () => (localStorage.getItem('shortcake-theme') as ThemeMode) ?? 'dark',
  );
  const prefersDark = useMediaQuery('(prefers-color-scheme: dark)');
  const resolvedTheme: 'dark' | 'light' =
    themeMode === 'system' ? (prefersDark ? 'dark' : 'light') : themeMode;

  useEffect(() => {
    localStorage.setItem('shortcake-theme', themeMode);
    document.documentElement.dataset.theme = resolvedTheme;
  }, [themeMode, resolvedTheme]);

  useEffect(() => {
    let cancelled = false;

    const loadStack = async () => {
      setIsStackLoading(true);
      setError(null);
      try {
        const data = await fetchJSON<StackResponse>('/api/stack');
        if (cancelled) return;

        setStack(data);

        const currentBranch = data.branches.find((b) => b.name === data.currentBranch);
        if (currentBranch) {
          setSelection({ type: 'branch', name: currentBranch.name });
        } else if (data.branches[0]) {
          setSelection({ type: 'branch', name: data.branches[0].name });
        } else {
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
      fileRefs.current[index]?.scrollIntoView({ behavior: 'smooth', block: 'start' });
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

  const handleHunkToggle = useCallback((key: HunkKey) => {
    setSelectedHunks((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

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

  return (
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
                  <span className="relative z-[2] flex items-center gap-[7px]">
                    <span className="text-[0.88rem] font-semibold whitespace-nowrap overflow-hidden text-ellipsis">
                      {branch.name}
                    </span>
                    {branch.isCurrent && (
                      <span className="font-mono text-[0.58rem] font-medium uppercase tracking-[0.05em] text-accent bg-accent/10 border border-accent/18 ml-1.5 px-[5px] py-px rounded-full shrink-0 leading-[1.5]">
                        current
                      </span>
                    )}
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
                    ) : (
                      <DiffFileSection
                        patch={patch}
                        fileInfo={info}
                        comments={comments}
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
                        selectedHunks={selectedHunks}
                        onHunkToggle={handleHunkToggle}
                        parsedHunks={parsedHunks}
                        diffStyle={diffStyle}
                        resolvedTheme={resolvedTheme}
                        onToggleViewed={toggleViewed}
                      />
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
                <span className="relative z-[2] flex items-center gap-[7px]">
                  <span className="text-[0.88rem] font-semibold whitespace-nowrap overflow-hidden text-ellipsis">
                    {branch.name}
                  </span>
                  {branch.isCurrent && (
                    <span className="font-mono text-[0.58rem] font-medium uppercase tracking-[0.05em] text-accent bg-accent/10 border border-accent/18 ml-1.5 px-[5px] py-px rounded-full shrink-0 leading-[1.5]">
                      current
                    </span>
                  )}
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
                  ) : (
                    <DiffFileSection
                      patch={patch}
                      fileInfo={info}
                      comments={comments}
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
                      selectedHunks={selectedHunks}
                      onHunkToggle={handleHunkToggle}
                      parsedHunks={parsedHunks}
                      diffStyle={diffStyle}
                      resolvedTheme={resolvedTheme}
                      onToggleViewed={toggleViewed}
                    />
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
          onAcceptInto={() => { setShowAcceptPicker(true); setAcceptError(null); }}
          onClear={() => setSelectedHunks(new Set())}
        />
      )}

      {selection?.type === 'branch' && selectedHunks.size > 0 && !showMovePicker && (
        <AcceptBanner
          count={selectedHunks.size}
          onAcceptInto={() => { setShowMovePicker(true); setMoveError(null); }}
          onClear={() => setSelectedHunks(new Set())}
          actionLabel="Move to..."
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
          />
        </div>
      )}
    </main>
  );
}
