; CodeLens's uninstaller, besides removing the app's own files
; (planning-v2.md Phase 11 step 7). electron-builder includes this file
; (package.json: build.nsis.include) and inserts customUnInstall after it has
; closed the running app and before it deletes the app's files.
;
; It runs the app once more, as `CodeLens.exe --uninstall` (src/uninstall.ts):
; that forgets start at login, removes the stack's containers, and asks
; whether to delete the data too, the data folder and the database volume,
; both or neither. Exit code 10 means it did; then the app's own Electron
; folder in %APPDATA% goes as well, as electron-builder's --delete-app-data
; would.
;
; Not when an update replaces this version: the old version's uninstaller
; runs then too (isUpdated), and must only swap the files.

!macro customUnInstall
  ${ifNot} ${isUpdated}
    ; The app runs from $TEMP, not from the folder about to be deleted:
    ; Electron's helper processes outlive it by a moment and keep their
    ; working folder busy, which left an empty install folder behind.
    SetOutPath $TEMP
    ; A one-click uninstall runs this section silently even when the user
    ; started it, so /S is read from the command line rather than ${Silent}.
    ClearErrors
    ${GetParameters} $R0
    ${GetOptions} $R0 "/S" $R1
    ${if} ${Errors}
      ExecWait '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --uninstall' $R2
    ${else}
      ExecWait '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --uninstall --silent' $R2
    ${endIf}
    ${if} $R2 == 10
      RMDir /r "$APPDATA\${APP_FILENAME}"
      !ifdef APP_PRODUCT_FILENAME
        RMDir /r "$APPDATA\${APP_PRODUCT_FILENAME}"
      !endif
      !ifdef APP_PACKAGE_NAME
        RMDir /r "$APPDATA\${APP_PACKAGE_NAME}"
      !endif
    ${endIf}
    ; electron-builder's copy of the installer, kept for updates (about
    ; 100 MB): nothing to update once the app is gone.
    RMDir /r "$LOCALAPPDATA\codelens-desktop-updater"
  ${endIf}
!macroend
