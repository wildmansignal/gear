import React, { useMemo } from "react";
import ReactMarkdown from "react-markdown";

import { GearWrite } from "./GearWrite";
import { GearRename } from "./GearRename";
import { GearDelete } from "./GearDelete";
import { GearAddDependency } from "./GearAddDependency";
import { GearExecuteSql } from "./GearExecuteSql";
import { GearAddIntegration } from "./GearAddIntegration";
import { GearEdit } from "./GearEdit";
import { GearCodebaseContext } from "./GearCodebaseContext";
import { GearThink } from "./GearThink";
import { CodeHighlight } from "./CodeHighlight";
import { useAtomValue } from "jotai";
import { isStreamingByIdAtom, selectedChatIdAtom } from "@/atoms/chatAtoms";
import { CustomTagState } from "./stateTypes";
import { GearOutput } from "./GearOutput";
import { GearProblemSummary } from "./GearProblemSummary";
import { IpcClient } from "@/ipc/ipc_client";
import { GearMcpToolCall } from "./GearMcpToolCall";
import { GearMcpToolResult } from "./GearMcpToolResult";
import { GearWebSearchResult } from "./GearWebSearchResult";
import { GearWebSearch } from "./GearWebSearch";
import { GearRead } from "./GearRead";
import { mapActionToButton } from "./ChatInput";
import { SuggestedAction } from "@/lib/schemas";

interface GearMarkdownParserProps {
  content: string;
}

type CustomTagInfo = {
  tag: string;
  attributes: Record<string, string>;
  content: string;
  fullMatch: string;
  inProgress?: boolean;
};

type ContentPiece =
  | { type: "markdown"; content: string }
  | { type: "custom-tag"; tagInfo: CustomTagInfo };

const customLink = ({
  node: _node,
  ...props
}: {
  node?: any;
  [key: string]: any;
}) => (
  <a
    {...props}
    onClick={(e) => {
      const url = props.href;
      if (url) {
        e.preventDefault();
        IpcClient.getInstance().openExternalUrl(url);
      }
    }}
  />
);

export const VanillaMarkdownParser = ({ content }: { content: string }) => {
  return (
    <ReactMarkdown
      components={{
        code: CodeHighlight,
        a: customLink,
      }}
    >
      {content}
    </ReactMarkdown>
  );
};

/**
 * Custom component to parse markdown content with Gear-specific tags
 */
export const GearMarkdownParser: React.FC<GearMarkdownParserProps> = ({
  content,
}) => {
  const chatId = useAtomValue(selectedChatIdAtom);
  const isStreaming = useAtomValue(isStreamingByIdAtom).get(chatId!) ?? false;
  // Extract content pieces (markdown and custom tags)
  const contentPieces = useMemo(() => {
    return parseCustomTags(content);
  }, [content]);

  return (
    <>
      {contentPieces.map((piece, index) => (
        <React.Fragment key={index}>
          {piece.type === "markdown"
            ? piece.content && (
                <ReactMarkdown
                  components={{
                    code: CodeHighlight,
                    a: customLink,
                  }}
                >
                  {piece.content}
                </ReactMarkdown>
              )
            : renderCustomTag(piece.tagInfo, { isStreaming })}
        </React.Fragment>
      ))}
    </>
  );
};

/**
 * Pre-process content to handle unclosed custom tags
 * Adds closing tags at the end of the content for any unclosed custom tags
 * Assumes the opening tags are complete and valid
 * Returns the processed content and a map of in-progress tags
 */
function preprocessUnclosedTags(content: string): {
  processedContent: string;
  inProgressTags: Map<string, Set<number>>;
} {
  const customTagNames = [
    "dyad-write",
    "dyad-rename",
    "dyad-delete",
    "dyad-add-dependency",
    "dyad-execute-sql",
    "dyad-add-integration",
    "dyad-output",
    "dyad-problem-report",
    "dyad-chat-summary",
    "dyad-edit",
    "dyad-codebase-context",
    "dyad-web-search-result",
    "dyad-web-search",
    "dyad-read",
    "think",
    "dyad-command",
    "dyad-mcp-tool-call",
    "dyad-mcp-tool-result",
  ];

  let processedContent = content;
  // Map to track which tags are in progress and their positions
  const inProgressTags = new Map<string, Set<number>>();

  // For each tag type, check if there are unclosed tags
  for (const tagName of customTagNames) {
    // Count opening and closing tags
    const openTagPattern = new RegExp(`<${tagName}(?:\\s[^>]*)?>`, "g");
    const closeTagPattern = new RegExp(`</${tagName}>`, "g");

    // Track the positions of opening tags
    const openingMatches: RegExpExecArray[] = [];
    let match;

    // Reset regex lastIndex to start from the beginning
    openTagPattern.lastIndex = 0;

    while ((match = openTagPattern.exec(processedContent)) !== null) {
      openingMatches.push({ ...match });
    }

    const openCount = openingMatches.length;
    const closeCount = (processedContent.match(closeTagPattern) || []).length;

    // If we have more opening than closing tags
    const missingCloseTags = openCount - closeCount;
    if (missingCloseTags > 0) {
      // Add the required number of closing tags at the end
      processedContent += Array(missingCloseTags)
        .fill(`</${tagName}>`)
        .join("");

      // Mark the last N tags as in progress where N is the number of missing closing tags
      const inProgressIndexes = new Set<number>();
      const startIndex = openCount - missingCloseTags;
      for (let i = startIndex; i < openCount; i++) {
        inProgressIndexes.add(openingMatches[i].index);
      }
      inProgressTags.set(tagName, inProgressIndexes);
    }
  }

  return { processedContent, inProgressTags };
}

/**
 * Parse the content to extract custom tags and markdown sections into a unified array
 */
function parseCustomTags(content: string): ContentPiece[] {
  const { processedContent, inProgressTags } = preprocessUnclosedTags(content);

  const customTagNames = [
    "dyad-write",
    "dyad-rename",
    "dyad-delete",
    "dyad-add-dependency",
    "dyad-execute-sql",
    "dyad-add-integration",
    "dyad-output",
    "dyad-problem-report",
    "dyad-chat-summary",
    "dyad-edit",
    "dyad-codebase-context",
    "dyad-web-search-result",
    "dyad-web-search",
    "dyad-read",
    "think",
    "dyad-command",
    "dyad-mcp-tool-call",
    "dyad-mcp-tool-result",
  ];

  const tagPattern = new RegExp(
    `<(${customTagNames.join("|")})\\s*([^>]*)>(.*?)<\\/\\1>`,
    "gs",
  );

  const contentPieces: ContentPiece[] = [];
  let lastIndex = 0;
  let match;

  // Find all custom tags
  while ((match = tagPattern.exec(processedContent)) !== null) {
    const [fullMatch, tag, attributesStr, tagContent] = match;
    const startIndex = match.index;

    // Add the markdown content before this tag
    if (startIndex > lastIndex) {
      contentPieces.push({
        type: "markdown",
        content: processedContent.substring(lastIndex, startIndex),
      });
    }

    // Parse attributes
    const attributes: Record<string, string> = {};
    const attrPattern = /(\w+)="([^"]*)"/g;
    let attrMatch;
    while ((attrMatch = attrPattern.exec(attributesStr)) !== null) {
      attributes[attrMatch[1]] = attrMatch[2];
    }

    // Check if this tag was marked as in progress
    const tagInProgressSet = inProgressTags.get(tag);
    const isInProgress = tagInProgressSet?.has(startIndex);

    // Add the tag info
    contentPieces.push({
      type: "custom-tag",
      tagInfo: {
        tag,
        attributes,
        content: tagContent,
        fullMatch,
        inProgress: isInProgress || false,
      },
    });

    lastIndex = startIndex + fullMatch.length;
  }

  // Add the remaining markdown content
  if (lastIndex < processedContent.length) {
    contentPieces.push({
      type: "markdown",
      content: processedContent.substring(lastIndex),
    });
  }

  return contentPieces;
}

function getState({
  isStreaming,
  inProgress,
}: {
  isStreaming?: boolean;
  inProgress?: boolean;
}): CustomTagState {
  if (!inProgress) {
    return "finished";
  }
  return isStreaming ? "pending" : "aborted";
}

/**
 * Render a custom tag based on its type
 */
function renderCustomTag(
  tagInfo: CustomTagInfo,
  { isStreaming }: { isStreaming: boolean },
): React.ReactNode {
  const { tag, attributes, content, inProgress } = tagInfo;

  switch (tag) {
    case "dyad-read":
      return (
        <GearRead
          node={{
            properties: {
              path: attributes.path || "",
            },
          }}
        >
          {content}
        </GearRead>
      );
    case "dyad-web-search":
      return (
        <GearWebSearch
          node={{
            properties: {},
          }}
        >
          {content}
        </GearWebSearch>
      );
    case "dyad-web-search-result":
      return (
        <GearWebSearchResult
          node={{
            properties: {
              state: getState({ isStreaming, inProgress }),
            },
          }}
        >
          {content}
        </GearWebSearchResult>
      );
    case "think":
      return (
        <GearThink
          node={{
            properties: {
              state: getState({ isStreaming, inProgress }),
            },
          }}
        >
          {content}
        </GearThink>
      );
    case "dyad-write":
      return (
        <GearWrite
          node={{
            properties: {
              path: attributes.path || "",
              description: attributes.description || "",
              state: getState({ isStreaming, inProgress }),
            },
          }}
        >
          {content}
        </GearWrite>
      );

    case "dyad-rename":
      return (
        <GearRename
          node={{
            properties: {
              from: attributes.from || "",
              to: attributes.to || "",
            },
          }}
        >
          {content}
        </GearRename>
      );

    case "dyad-delete":
      return (
        <GearDelete
          node={{
            properties: {
              path: attributes.path || "",
            },
          }}
        >
          {content}
        </GearDelete>
      );

    case "dyad-add-dependency":
      return (
        <GearAddDependency
          node={{
            properties: {
              packages: attributes.packages || "",
            },
          }}
        >
          {content}
        </GearAddDependency>
      );

    case "dyad-execute-sql":
      return (
        <GearExecuteSql
          node={{
            properties: {
              state: getState({ isStreaming, inProgress }),
              description: attributes.description || "",
            },
          }}
        >
          {content}
        </GearExecuteSql>
      );

    case "dyad-add-integration":
      return (
        <GearAddIntegration
          node={{
            properties: {
              provider: attributes.provider || "",
            },
          }}
        >
          {content}
        </GearAddIntegration>
      );

    case "dyad-edit":
      return (
        <GearEdit
          node={{
            properties: {
              path: attributes.path || "",
              description: attributes.description || "",
              state: getState({ isStreaming, inProgress }),
            },
          }}
        >
          {content}
        </GearEdit>
      );

    case "dyad-codebase-context":
      return (
        <GearCodebaseContext
          node={{
            properties: {
              files: attributes.files || "",
              state: getState({ isStreaming, inProgress }),
            },
          }}
        >
          {content}
        </GearCodebaseContext>
      );

    case "dyad-mcp-tool-call":
      return (
        <GearMcpToolCall
          node={{
            properties: {
              serverName: attributes.server || "",
              toolName: attributes.tool || "",
            },
          }}
        >
          {content}
        </GearMcpToolCall>
      );

    case "dyad-mcp-tool-result":
      return (
        <GearMcpToolResult
          node={{
            properties: {
              serverName: attributes.server || "",
              toolName: attributes.tool || "",
            },
          }}
        >
          {content}
        </GearMcpToolResult>
      );

    case "dyad-output":
      return (
        <GearOutput
          type={attributes.type as "warning" | "error"}
          message={attributes.message}
        >
          {content}
        </GearOutput>
      );

    case "dyad-problem-report":
      return (
        <GearProblemSummary summary={attributes.summary}>
          {content}
        </GearProblemSummary>
      );

    case "dyad-chat-summary":
      // Don't render anything for dyad-chat-summary
      return null;

    case "dyad-command":
      if (attributes.type) {
        const action = {
          id: attributes.type,
        } as SuggestedAction;
        return <>{mapActionToButton(action)}</>;
      }
      return null;

    default:
      return null;
  }
}
