// Retroport Workbench.exe: starts the workbench with no console window (pythonw) and exits.
//
// In a release it runs the Python that comes with it (runtime\pythonw.exe) on the workbench's code
// (app\); in a source checkout, the pythonw.exe on PATH on the code next to it. The workbench then
// opens its own window (retroport\workbench\app.py).
//
// Built by packaging\build_release.py with the C# compiler every Windows has (.NET Framework 4).

using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("Retroport Workbench")]
[assembly: System.Reflection.AssemblyProduct("Retroport Workbench")]
[assembly: System.Reflection.AssemblyDescription("Browse retail WoW items and backport them to a 3.3.5a client")]

static class Launcher
{
    const string Title = "Retroport Workbench";

    [STAThread]
    static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, "runtime", "pythonw.exe");
        string code = Path.Combine(root, "app");
        if (!File.Exists(python))
        {
            python = OnPath("pythonw.exe");
            code = root;
        }
        if (python == null)
        {
            Fail("Python is missing: this program needs the runtime folder that comes with it.\n\n" +
                 "Unpack the whole Retroport Workbench zip and run it from that folder.");
            return 1;
        }
        if (!Directory.Exists(Path.Combine(code, "retroport")))
        {
            Fail("The workbench's files are missing next to this program (" + code + ").\n\n" +
                 "Unpack the whole Retroport Workbench zip and run it from that folder.");
            return 1;
        }
        ProcessStartInfo start = new ProcessStartInfo(python, "-m retroport.workbench.app");
        start.WorkingDirectory = code;
        start.UseShellExecute = false;
        start.EnvironmentVariables["PYTHONPATH"] = code;
        try
        {
            Process.Start(start);
        }
        catch (Exception e)
        {
            Fail("The workbench could not start:\n\n" + e.Message);
            return 1;
        }
        return 0;
    }

    static string OnPath(string exe)
    {
        foreach (string dir in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
        {
            try
            {
                string full = Path.Combine(dir.Trim().Trim('"'), exe);
                if (File.Exists(full))
                    return full;
            }
            catch (ArgumentException)
            {
            }
        }
        return null;
    }

    static void Fail(string text)
    {
        MessageBox.Show(text, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
