-- notes_append.applescript  [MUTATE — approval-gated]
-- Append a paragraph to a note by exact title, creating the note if absent.
-- Usage: scripts/osa.sh -f scripts/notes_append.applescript "Note Title" "Text to append"
-- Notes bodies are HTML: this script wraps the text in <div>…</div>. If the text
-- may contain < > &, escape it BEFORE calling (see SKILL.md gotchas).
-- First-ever run pops a one-time TCC consent dialog for Notes — run attended.
on run argv
	if (count of argv) < 2 then error "usage: notes_append <note-title> <text>" number 64
	set noteTitle to item 1 of argv
	set addition to item 2 of argv
	tell application "Notes"
		if not running then launch
		set matches to notes of default account whose name is noteTitle
		if (count of matches) is 0 then
			make new note at default account with properties {name:noteTitle, body:"<div>" & addition & "</div>"}
			return "created note: " & noteTitle
		else
			set theNote to item 1 of matches
			set body of theNote to (body of theNote) & "<div>" & addition & "</div>"
			return "appended to note: " & noteTitle
		end if
	end tell
end run
