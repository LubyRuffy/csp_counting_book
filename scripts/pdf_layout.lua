-- PDF-only pagination hints for code blocks.

function CodeBlock(block)
  if not FORMAT:match("latex") then
    return nil
  end

  local _, line_count = block.text:gsub("\n", "\n")
  line_count = line_count + 1

  local reserved_lines
  if line_count <= 28 then
    -- Code uses a smaller font and 0.90 line spacing. Convert its estimated
    -- height to normal body baselines before asking needspace for room.
    reserved_lines = math.max(5, math.ceil(line_count * 0.62) + 3)
  else
    -- Long samples must split, but should not start at the foot of a page.
    reserved_lines = 12
  end

  return {
    pandoc.RawBlock(
      "latex",
      string.format("\\Needspace{%d\\baselineskip}", reserved_lines)
    ),
    block,
  }
end
