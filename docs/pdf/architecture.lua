-- Pandoc filter that turns the architecture Markdown into one printable
-- document. The diagrams page becomes the front part, with each diagram and
-- its description on a page of its own, landscape or portrait from the
-- diagram's shape. Links between the files become links inside the document.
-- A link or image the document cannot resolve stops the build, so the PDF
-- never carries a dead link or a missing diagram.
--
-- Chrome prints the HTML this produces with the local files of the build
-- machine in reach. So the Markdown may carry no raw content, no image other
-- than a diagram in diagrams/, no key=value attribute (style, event handlers)
-- and no metadata of its own: each would let a docs change frame a local file,
-- run a script or fetch from the network into the PDF.
--
-- build-pdf.sh lists the diagrams it checked (regular files, not links) in the
-- file ARCHITECTURE_DIAGRAMS names. A diagram is opened only under a name on
-- that list, spelled exactly: on a case-insensitive file system another
-- spelling could reach an entry the list does not hold.

local DIAGRAM_SRC = "^diagrams/([%w_%-]+)%.svg$"
-- The metadata build-pdf.sh sets on the command line; nothing else is read.
local ALLOWED_METADATA = { title = true, subtitle = true }

-- Raw blocks and spans are the only elements pandoc copies into the page
-- unescaped. Its HTML writer copies html, html4, html5 and the slide formats
-- (revealjs, s5, slidy, slideous, dzslides) as they stand, and any other
-- format is no use in the PDF, so none is allowed.
local function refuse_raw(raw)
  error("raw HTML is not allowed in the architecture docs, nor raw content in any other format: {="
    .. raw.format .. "} " .. raw.text:sub(1, 60))
end

RawBlock = refuse_raw
RawInline = refuse_raw

local function refuse_attributes(el)
  if #el.attributes > 0 then
    error("the attribute " .. el.attributes[1][1] .. " is not allowed in the architecture docs")
  end
end

local ATTRIBUTE_CHECKS = {}
for _, name in ipairs({ "Code", "CodeBlock", "Div", "Figure", "Header", "Image", "Link", "Span", "Table" }) do
  ATTRIBUTE_CHECKS[name] = refuse_attributes
end

local function basename(path)
  return path:match("([^/\\]+)$")
end

local checked_diagrams = nil

local function read_checked_diagrams()
  local list = os.getenv("ARCHITECTURE_DIAGRAMS")
  if not list or list == "" then
    error("ARCHITECTURE_DIAGRAMS names no list of checked diagrams; build the PDF with build-pdf.sh")
  end
  local file = io.open(list, "r")
  if not file then
    error("cannot open the list of checked diagrams: " .. list)
  end
  checked_diagrams = {}
  for name in file:lines() do
    checked_diagrams[name] = true
  end
  file:close()
end

-- Opens the diagram an image source names, only under a name on the checked
-- list.
local function open_diagram(src)
  local name = src:match(DIAGRAM_SRC)
  if not name then
    error("image outside diagrams/: " .. src)
  end
  local file = checked_diagrams[name .. ".svg"] and io.open("diagrams/" .. name .. ".svg", "rb")
  if not file then
    error("image not found: " .. src .. " (a diagram is read only under its exact name, as a regular file)")
  end
  return file
end

local function svg_orientation(path)
  local file = open_diagram(path)
  local head = file:read(4096) or ""
  file:close()
  local tag = head:match("<svg[^>]*>") or ""
  local width = tonumber(tag:match(' width="([%d.]+)'))
  local height = tonumber(tag:match(' height="([%d.]+)'))
  if not width or not height then
    error("no width and height on the svg element of " .. path)
  end
  return height > width and "port" or "land"
end

local function diagram_in(blocks)
  local found = {}
  pandoc.Div(blocks):walk({
    Image = function(image)
      local name = image.src:match(DIAGRAM_SRC)
      if name then
        table.insert(found, { name = name, src = image.src })
      end
    end,
  })
  if #found > 1 then
    error("one diagram per section; found " .. #found .. " under one heading")
  end
  return found[1]
end

-- The diagrams page: its title and version line give way to the cover, each
-- level-3 section that holds a diagram becomes a page, and every heading moves
-- up one level so the page's sections sit beside the overview and threat model.
local function front_part(blocks)
  local start = 2
  if blocks[2] and blocks[2].t == "Para" and pandoc.utils.stringify(blocks[2]):match("^Version:") then
    start = 3
  end
  local out = pandoc.Blocks({})
  local group = nil

  local function flush()
    if not group then
      return
    end
    local diagram = diagram_in(group)
    if diagram then
      local orientation = svg_orientation(diagram.src)
      out:insert(pandoc.Div(group, { id = "diagram-" .. diagram.name, class = "diagram " .. orientation }))
    else
      out:extend(group)
    end
    group = nil
  end

  for i = start, #blocks do
    local block = blocks[i]
    if block.t == "Header" and block.level <= 3 then
      flush()
      if block.level == 3 then
        group = pandoc.Blocks({})
      end
    end
    if group then
      group:insert(block)
    else
      out:insert(block)
    end
  end
  flush()

  return out:walk({
    Header = function(header)
      header.level = header.level - 1
      return header
    end,
  })
end

function Pandoc(doc)
  local files = PANDOC_STATE.input_files
  local front, rest = pandoc.Blocks({}), pandoc.Blocks({})
  local file_ids = {}
  local level_one = 0

  read_checked_diagrams()
  for key, _ in pairs(doc.meta) do
    if not ALLOWED_METADATA[key] then
      error("document metadata is not allowed in the architecture docs: " .. key)
    end
  end
  doc.blocks:walk(ATTRIBUTE_CHECKS)

  if not (doc.blocks[1] and doc.blocks[1].t == "Header" and doc.blocks[1].level == 1) then
    error("the first input file must open with a level-1 heading")
  end
  for _, block in ipairs(doc.blocks) do
    if block.t == "Header" and block.level == 1 then
      level_one = level_one + 1
      if level_one > 1 and files[level_one] then
        file_ids[basename(files[level_one])] = block.identifier
      end
    end
    if level_one == 1 then
      front:insert(block)
    else
      rest:insert(block)
    end
  end
  if level_one ~= #files then
    error("expected one level-1 heading per input file: " .. #files .. " files, " .. level_one .. " headings")
  end

  local blocks = front_part(front)
  blocks:extend(rest)

  local ids = {}
  blocks:walk({
    Header = function(header)
      ids[header.identifier] = true
    end,
    Div = function(div)
      if div.identifier ~= "" then
        ids[div.identifier] = true
      end
    end,
  })

  local function internal(target)
    if target:match("^%a[%w+.%-]*:") then
      return target
    end
    local id
    local diagram = target:match(DIAGRAM_SRC)
    if diagram then
      id = "diagram-" .. diagram
    else
      local file, fragment = target:match("^([^#]*)#?(.*)$")
      if file == "" then
        id = fragment
      elseif file_ids[file] then
        id = fragment ~= "" and fragment or file_ids[file]
      end
    end
    if not id or not ids[id] then
      error("link target not in the document: " .. target)
    end
    return "#" .. id
  end

  blocks = blocks:walk({
    Link = function(link)
      -- A diagram wrapped in a link to itself opens full size on GitHub; on
      -- the page it is already full size.
      if #link.content == 1 and link.content[1].t == "Image" and link.content[1].src == link.target then
        return link.content[1]
      end
      link.target = internal(link.target)
      return link
    end,
    Image = function(image)
      open_diagram(image.src):close()
    end,
  })

  -- Each diagram goes into the page as a data: URL, after the links are
  -- resolved against the file names, so the printed page reads no file.
  blocks = blocks:walk({
    Image = function(image)
      local file = open_diagram(image.src)
      local svg = file:read("a")
      file:close()
      image.src = "data:image/svg+xml," .. svg:gsub("[^%w%-%._~]", function(c)
        return string.format("%%%02X", c:byte())
      end)
      return image
    end,
  })

  return pandoc.Pandoc(blocks, doc.meta)
end
