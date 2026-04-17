@rem Minimal Gradle wrapper launcher for Windows
@echo off
setlocal

set APP_HOME=%~dp0
set CLASSPATH=%APP_HOME%gradle\wrapper\gradle-wrapper.jar
if exist "%APP_HOME%gradle\\wrapper\\gradle-wrapper-shared.jar" (
  set CLASSPATH=%CLASSPATH%;%APP_HOME%gradle\wrapper\gradle-wrapper-shared.jar
)

if not exist "%CLASSPATH%" (
  echo Missing %CLASSPATH%
  exit /b 1
)

java -classpath "%CLASSPATH%" org.gradle.wrapper.GradleWrapperMain %*
