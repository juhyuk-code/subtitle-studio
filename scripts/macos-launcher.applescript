on run
	set appPath to POSIX path of (path to me)
	set projectPath to do shell script "/usr/bin/dirname " & quoted form of appPath
	set launcherPath to projectPath & "/Start Subtitle Studio.command"
	
	tell application "Terminal"
		activate
		do script "cd " & quoted form of projectPath & " && exec /bin/bash " & quoted form of launcherPath
	end tell
end run
