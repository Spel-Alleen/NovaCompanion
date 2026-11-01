using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;
class Launcher {
    static string Quote(string value) {
        StringBuilder result = new StringBuilder("\""); int slash = 0;
        foreach (char c in value) {
            if (c == '\\') { slash++; continue; }
            if (c == '"') { result.Append('\\', slash * 2 + 1); result.Append(c); slash = 0; continue; }
            result.Append('\\', slash); slash = 0; result.Append(c);
        }
        result.Append('\\', slash * 2); result.Append('"'); return result.ToString();
    }
    [STAThread] static int Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        try {
            string python = Path.Combine(root, "runtime", "pythonw.exe");
            string script = Path.Combine(root, "source", "app.py");
            if (!File.Exists(python) || !File.Exists(script)) throw new Exception("Оставь папки runtime, source и agent рядом с NovaCompanion.exe.");
            StringBuilder command = new StringBuilder(Quote(script));
            foreach (string arg in args) command.Append(" ").Append(Quote(arg));
            ProcessStartInfo start = new ProcessStartInfo(python, command.ToString());
            start.WorkingDirectory = root; start.UseShellExecute = false; start.CreateNoWindow = true;
            using (Process child = Process.Start(start)) { child.WaitForExit(); return child.ExitCode; }
        } catch (Exception error) {
            MessageBox.Show(error.Message, "Nova Companion", MessageBoxButtons.OK, MessageBoxIcon.Error); return 1;
        }
    }
}
