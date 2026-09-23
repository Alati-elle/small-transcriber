on open droppedFiles

	set homePath to POSIX path of (path to home folder)
	set guiBinary to homePath & ".local/bin/gemini_meeting_gui"

	repeat with inputFile in droppedFiles
		set inputPath to POSIX path of inputFile

		do shell script "/usr/bin/nohup " & quoted form of guiBinary & " " & quoted form of inputPath & " >/tmp/gemini_meeting_gui.log 2>&1 </dev/null &"
	end repeat

	quit

end open


on run

	display dialog "Перетащите аудиофайл на приложение «Расшифровать встречу»." buttons {"OK"} default button "OK"

	quit

end run
