-- Apple Books 深色主题会改写作者颜色；这个专用类让代码和重点文字保留可读配色。
function CodeBlock(block)
  return pandoc.Div(
    { block },
    pandoc.Attr("", { "ibooks-dark-theme-use-custom-text-color" })
  )
end

function Strong(strong)
  return pandoc.Span(
    { strong },
    pandoc.Attr(
      "",
      { "book-emphasis", "ibooks-dark-theme-use-custom-text-color" }
    )
  )
end
