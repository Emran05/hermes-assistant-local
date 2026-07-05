-- reminder_add.applescript  [MUTATE — approval-gated]
-- Create a reminder with a due date built from AppleScript date OBJECTS —
-- never from locale-dependent date-string parsing (silently wrong off en_US).
-- Usage: scripts/osa.sh -f scripts/reminder_add.applescript "Title" YYYY MM DD HH24 MIN
--   e.g. ... "Call dentist" 2026 7 10 9 0
-- Returns a 12-hour confirmation string — echo it back to the user verbatim.
-- First-ever run pops a one-time TCC consent dialog for Reminders — run attended.
on run argv
	if (count of argv) < 6 then error "usage: reminder_add <title> <yyyy> <mm> <dd> <hh24> <min>" number 64
	set theTitle to item 1 of argv
	set y to (item 2 of argv) as integer
	set mo to (item 3 of argv) as integer
	set dd to (item 4 of argv) as integer
	set hh to (item 5 of argv) as integer
	set mins to (item 6 of argv) as integer

	-- Build the date object safely: day 1 first so month arithmetic can't overflow
	set dueDate to current date
	set day of dueDate to 1
	set year of dueDate to y
	set month of dueDate to mo
	set day of dueDate to dd
	set time of dueDate to hh * hours + mins * minutes

	tell application "Reminders"
		if not running then launch
		make new reminder with properties {name:theTitle, due date:dueDate, remind me date:dueDate}
	end tell

	-- 12-hour confirmation (house style: always confirm times 12-hour)
	set hh12 to hh mod 12
	if hh12 = 0 then set hh12 to 12
	set ampm to "AM"
	if hh is greater than or equal to 12 then set ampm to "PM"
	set mm2 to text -2 thru -1 of ("0" & (mins as text))
	set wkd to weekday of dueDate as text
	set mon to month of dueDate as text
	return "created reminder \"" & theTitle & "\" due " & (text 1 thru 3 of wkd) & " " & (text 1 thru 3 of mon) & " " & dd & " " & hh12 & ":" & mm2 & " " & ampm
end run
