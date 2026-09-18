// Original Sierra Romeo installer code, GPL-3.0-only.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("Sierra Romeo Setup")]
[assembly: AssemblyProduct("Sierra Romeo")]
[assembly: AssemblyDescription("Guided setup from your own game dump")]
[assembly: AssemblyVersion("0.1.0.0")]
[assembly: AssemblyFileVersion("0.1.0.0")]
[assembly: AssemblyInformationalVersion("0.1.0-alpha.2")]

internal static class Program {
    [STAThread] static int Main(string[] args) {
        try {
            // Release verification uses the SAME extraction routine as the wizard.
            if (args.Length == 2 && args[0] == "--verify-extract") {
                Payload.Extract(args[1]); return 0;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new SetupWizard()); return 0;
        } catch (Exception ex) {
            if (args.Length > 0) { Console.Error.WriteLine(ex.ToString()); return 1; }
            MessageBox.Show(ex.Message, "Sierra Romeo setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}

internal static class Payload {
    internal static string Version {
        get { using (var r = new StreamReader(Assembly.GetExecutingAssembly().GetManifestResourceStream("version.json"))) {
            return (string)new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(r.ReadToEnd())["version"];
        } }
    }
    internal static void Extract(string destination) {
        string root = Path.GetFullPath(destination).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (Directory.Exists(root) || File.Exists(root.TrimEnd(Path.DirectorySeparatorChar)))
            throw new IOException("Choose a new, empty installation location. Existing files will not be replaced.");
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("source.zip"))
        using (var zip = new ZipArchive(stream, ZipArchiveMode.Read)) {
            // Validate the complete inventory and hashes BEFORE writing any payload file.
            var manifestEntry = zip.GetEntry("SOURCE-MANIFEST.json");
            if (manifestEntry == null) throw new IOException("Source manifest is missing.");
            Dictionary<string,object> manifest;
            using (var reader = new StreamReader(manifestEntry.Open()))
                manifest = new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(reader.ReadToEnd());
            var expected = new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
            foreach (var item in (System.Collections.ArrayList)manifest["files"]) {
                var row = (Dictionary<string,object>)item;
                expected.Add((string)row["path"], (string)row["sha256"]);
            }
            if (zip.Entries.Count != expected.Count + 1) throw new IOException("Unexpected source payload files.");
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var entry in zip.Entries) {
                string path = Path.GetFullPath(Path.Combine(root, entry.FullName));
                if (!path.StartsWith(root, StringComparison.OrdinalIgnoreCase) || entry.FullName.Contains(":") || !seen.Add(entry.FullName))
                    throw new IOException("Unsafe source payload path.");
                if (entry.FullName == "SOURCE-MANIFEST.json") continue;
                string hash;
                if (!expected.TryGetValue(entry.FullName, out hash)) throw new IOException("Unlisted payload entry.");
                using (var sha = SHA256.Create()) using (var input = entry.Open()) {
                    string actual = BitConverter.ToString(sha.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
                    if (hash != actual) throw new IOException("Damaged source payload: " + entry.FullName);
                }
            }
            Directory.CreateDirectory(root);
            foreach (var entry in zip.Entries) {
                string path = Path.Combine(root, entry.FullName);
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                using (var input = entry.Open()) using (var output = new FileStream(path, FileMode.CreateNew)) input.CopyTo(output);
            }
        }
    }
}

internal sealed class SetupWizard : Form {
    readonly Color paper = Color.FromArgb(23,27,32), panel = Color.FromArgb(32,38,44), accent = Color.FromArgb(220,171,82);
    readonly Panel body = new Panel();
    readonly Label step = new Label(), heading = new Label(), status = new Label();
    readonly TextBox destination = new TextBox(), iso = new TextBox(), output = new TextBox();
    readonly CheckBox ownership = new CheckBox(), dependencies = new CheckBox(), desktop = new CheckBox(), startMenu = new CheckBox(), fullscreen = new CheckBox();
    readonly Button back = new Button(), next = new Button(), close = new Button();
    readonly ProgressBar progress = new ProgressBar();
    readonly List<Control> options = new List<Control>();
    int page = 0;
    bool running, installed, extracted, warnings;
    string installRoot, logPath;
    readonly Label explanation = new Label();

    internal SetupWizard() {
        Text = "Sierra Romeo Setup | " + Payload.Version;
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        ClientSize = new Size(900,700); MinimumSize = new Size(916,739);
        StartPosition = FormStartPosition.CenterScreen; BackColor = paper; ForeColor = Color.WhiteSmoke;
        Font = new Font("Segoe UI", 10); AutoScaleMode = AutoScaleMode.Dpi;
        AcceptButton=next; CancelButton=close; step.UseMnemonic=false;
        var banner = new Panel { Dock=DockStyle.Top, Height=135, BackColor=panel };
        var picture = new PictureBox { Left=22, Top=10, Width=195, Height=115, SizeMode=PictureBoxSizeMode.Zoom };
        using (var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("header.png"))
        using (var image = Image.FromStream(s)) picture.Image = new Bitmap(image);
        banner.Controls.Add(picture);
        banner.Controls.Add(new Label { Text="SIERRA ROMEO", Left=240, Top=28, Width=610, Height=42, Font=new Font("Segoe UI Semibold",24), ForeColor=accent });
        banner.Controls.Add(new Label { Text="A fan port (and expansion!) of the original Army of Two (2008) for PC.   /   " + Payload.Version, Left=244, Top=80, Width=610, Height=30 });
        Controls.Add(banner);
        var footer = new Panel { Width=ClientSize.Width, Dock=DockStyle.Bottom, Height=68, BackColor=panel };
        StyleButton(back,"Back",550); StyleButton(next,"Next",660); StyleButton(close,"Close",770);
        footer.Controls.AddRange(new Control[]{back,next,close}); Controls.Add(footer);
        body.Dock=DockStyle.Fill; body.Padding=new Padding(32); Controls.Add(body); body.BringToFront();
        step.SetBounds(32,18,810,26); step.ForeColor=accent;
        heading.SetBounds(32,50,810,40); heading.Font=new Font("Segoe UI Semibold",19);
        explanation.SetBounds(32,99,820,66);
        body.Controls.AddRange(new Control[]{step,heading,explanation});
        back.Click += delegate { page--; ShowPage(); };
        next.Click += async delegate {
            if (installed) { Launch(); return; }
            if (page==0) { if (!ValidateInputs()) return; page=1; ShowPage(); }
            else if (page==1) { page=2; ShowPage(); await Install(); }
            else if (!running) { await Install(); }
        };
        close.Click += delegate { if(running) RequestCancel(); else Close(); };
        FormClosing += delegate(object sender, FormClosingEventArgs e) {
            if (running) { e.Cancel=true; RequestCancel(); }
        };
        destination.Text=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"SierraRomeo");
        ShowPage();
    }
    void StyleButton(Button button,string text,int left) {
        button.Text=text; button.SetBounds(left,16,100,36); button.FlatStyle=FlatStyle.Flat;
        button.ForeColor=Color.WhiteSmoke; button.BackColor=paper; button.Anchor=AnchorStyles.Right|AnchorStyles.Top;
    }
    void Add(Control c,int x,int y,int w,int h) { c.SetBounds(x,y,w,h); body.Controls.Add(c); options.Add(c); }
    void Field(string label,TextBox box,int y,EventHandler browse) {
        Add(new Label{Text=label},32,y,800,25);
        box.BackColor=panel; box.ForeColor=Color.White; box.BorderStyle=BorderStyle.FixedSingle;
        Add(box,32,y+28,700,28);
        var button=new Button{Text="Browse...",FlatStyle=FlatStyle.Flat,BackColor=panel};
        button.Click+=browse; Add(button,745,y+26,105,32);
    }
    void Check(CheckBox box,string text,int y,bool initial) {
        if (box.Text.Length==0) box.Checked=initial;
        box.Text=text; Add(box,36,y,805,32);
    }
    void ShowPage() {
        foreach(var c in options) body.Controls.Remove(c); options.Clear();
        back.Visible=page==1; next.Enabled=true; close.Enabled=true;
        step.Text=page==0 ? "01  /  GAME & LOCATION" : page==1 ? "02  /  MAKE IT YOURS" : "03  /  INSTALLATION";
        if(page==0) {
            heading.Text="Let's get ready.";
            explanation.Text="Select your legally-owned USA Xbox 360 disc image and a new installation folder.\nWindows x64 with a DirectX 12 GPU is required. No game files are included with this installer.";
            Field("Game disc image (*.iso):",iso,176,delegate {
                using(var d=new OpenFileDialog{Filter="Xbox 360 disc image|*.iso",Title="Select your own Army of Two dump"})
                    if(d.ShowDialog(this)==DialogResult.OK) iso.Text=d.FileName;
            });
            Field("Install to:",destination,255,delegate {
                using(var d=new FolderBrowserDialog{Description="Choose a parent folder. Setup will create SierraRomeo inside it."})
                    if(d.ShowDialog(this)==DialogResult.OK) destination.Text=Path.Combine(d.SelectedPath,"SierraRomeo");
            });
            Check(ownership,"I own this game and am using my own lawfully obtained dump.",344,false);
            Add(new Label{Text="~25 GB free recommended, plus space for missing Microsoft tools on the system drive.\nFirst installation downloads tools and compiles locally, so it may take a while.",ForeColor=Color.Silver},36,388,812,60);
            next.Text="Next";
        } else if(page==1) {
            heading.Text="Choose your setup.";
            explanation.Text="All downloads are version-pinned and hash-checked. Compatible tools already on this PC are reused.\nMicrosoft build tools may need Windows permission.";
            Check(dependencies,"Download and install missing build tools (vendor licenses apply)",175,true);
            Check(desktop,"Create a desktop shortcut",216,true);
            Check(startMenu,"Create a Start menu shortcut",257,true);
            Check(fullscreen,"Start in fullscreen",298,true);
            var license=new LinkLabel{Text="View project and dependency licenses",LinkColor=accent};
            license.Click+=delegate { ShowLicenses(); }; Add(license,36,349,740,30);
            next.Text="Install";
        } else {
            heading.Text="Building your PC edition...";
            explanation.Text="Your original image and existing saves are left untouched. Detailed progress is recorded below.\nPlease keep this window open. (No direct CLI action is needed.)";
            Add(status,32,170,820,40); status.Text="Preparing...";
            Add(progress,32,215,820,18); progress.Style=ProgressBarStyle.Marquee;
            output.Multiline=true; output.ReadOnly=true; output.ScrollBars=ScrollBars.Vertical;
            output.BackColor=panel; output.ForeColor=Color.Gainsboro; output.Font=new Font("Consolas",9);
            Add(output,32,255,820,170);
            var logs=new LinkLabel{Text="Open installation folder / logs",LinkColor=accent};
            logs.Click+=delegate { if(installRoot!=null && Directory.Exists(installRoot)) Process.Start(installRoot); };
            Add(logs,32,438,650,28); next.Text="Retry";
        }
    }
    bool ValidateInputs() {
        try {
            if(!Environment.Is64BitOperatingSystem || Environment.GetEnvironmentVariable("PROCESSOR_ARCHITEW6432")=="ARM64" || Environment.GetEnvironmentVariable("PROCESSOR_ARCHITECTURE")=="ARM64")
                throw new Exception("This preview supports Windows x64, not ARM64.");
            if(!ownership.Checked) throw new Exception("Please confirm that you own the game and dump.");
            if(!File.Exists(iso.Text) || !String.Equals(Path.GetExtension(iso.Text),".iso",StringComparison.OrdinalIgnoreCase)) throw new Exception("Select an existing .iso disc image.");
            installRoot=Path.GetFullPath(destination.Text).TrimEnd('\\');
            extracted=false;
            if(Directory.Exists(installRoot)) {
                string record=Path.Combine(installRoot,"artifacts","installer-options.json");
                string manifest=Path.Combine(installRoot,"SOURCE-MANIFEST.json");
                if(!File.Exists(record) || !File.Exists(manifest)) throw new Exception("Select a new folder. This folder is not an installation created by this wizard.");
                using(var s=Assembly.GetExecutingAssembly().GetManifestResourceStream("source.zip"))
                using(var z=new ZipArchive(s,ZipArchiveMode.Read))
                using(var r=new StreamReader(z.GetEntry("SOURCE-MANIFEST.json").Open()))
                    if(File.ReadAllText(manifest)!=r.ReadToEnd()) throw new Exception("This folder belongs to a different release. Choose a new location.");
                if(MessageBox.Show(this,"Resume setup in this folder? Existing files and saves will be retained.","Resume installation",MessageBoxButtons.YesNo)!=DialogResult.Yes) return false;
                extracted=true;
            }
            if(File.Exists(installRoot)) throw new Exception("The installation location is a file. Choose a folder.");
            if(installRoot.StartsWith("\\\\") || installRoot.Length>120 || installRoot.Contains("\"")) throw new Exception("Choose a local installation path shorter than 120 characters.");
            var drive=new DriveInfo(Path.GetPathRoot(installRoot));
            if(!extracted && drive.AvailableFreeSpace < 15L*1024*1024*1024) throw new Exception("Choose a drive with at least 15 GB free (25 GB recommended) for extraction and compilation.");
            return true;
        } catch(Exception ex) { MessageBox.Show(this,ex.Message,"Check installation choices",MessageBoxButtons.OK,MessageBoxIcon.Information); return false; }
    }
    void ShowLicenses() {
        var form=new Form{Text="Licenses & Downloads",Icon=this.Icon,Size=new Size(760,550),StartPosition=FormStartPosition.CenterParent};
        var text=new TextBox{Multiline=true,ReadOnly=true,ScrollBars=ScrollBars.Vertical,Dock=DockStyle.Fill};
        using(var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("source.zip"))
        using(var zip=new ZipArchive(stream,ZipArchiveMode.Read))
        foreach(string name in new[]{"THIRD_PARTY_NOTICES.md","LICENSE"}) {
            var entry=zip.GetEntry(name); if(entry==null) continue;
            using(var reader=new StreamReader(entry.Open())) text.AppendText(name+"\r\n\r\n"+reader.ReadToEnd().Replace("\n","\r\n")+"\r\n\r\n");
        }
        form.Controls.Add(text); form.ShowDialog(this); form.Dispose();
    }
    void Line(string line) {
        if(String.IsNullOrEmpty(line) || IsDisposed) return;
        BeginInvoke((Action)delegate {
            if(output.TextLength>60000) output.Text=output.Text.Substring(output.TextLength-40000);
            output.AppendText(line+Environment.NewLine);
            if(line.StartsWith("AOT_STAGE|")) status.Text=line.Substring(10);
            if(line.StartsWith("AOT_WARNING|")) warnings=true;
        });
    }
    void RequestCancel() {
        if(!extracted) { MessageBox.Show(this,"Please wait for the source package to finish opening.","Preparing setup"); return; }
        if(MessageBox.Show(this,"Stop after the current step finishes? Vendor installers and active compilation will not be forcibly interrupted. You can retry later.","Cancel setup",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes) return;
        if(installRoot!=null) {
            Directory.CreateDirectory(Path.Combine(installRoot,"artifacts"));
            File.WriteAllText(Path.Combine(installRoot,"artifacts","setup.cancel"),"cancel");
            status.Text="Stopping after the current step..."; close.Enabled=false;
        }
    }
    async Task Install() {
        running=true; warnings=false; next.Text="Installing..."; next.Enabled=false; close.Enabled=false; back.Visible=false;
        string selectedIso=Path.GetFullPath(iso.Text);
        var settings=new Dictionary<string,object>{{"iso",selectedIso},{"installDependencies",dependencies.Checked},{"desktop",desktop.Checked},{"startMenu",startMenu.Checked},{"fullscreen",fullscreen.Checked}};
        try {
            await Task.Run(delegate {
                if(!extracted) { Payload.Extract(installRoot); extracted=true; }
                string artifacts=Path.Combine(installRoot,"artifacts"); Directory.CreateDirectory(artifacts);
                File.Delete(Path.Combine(artifacts,"setup.cancel"));
                BeginInvoke((Action)delegate { close.Text="Cancel"; close.Enabled=true; });
                string choices=Path.Combine(artifacts,"installer-options.json");
                File.WriteAllText(choices,new JavaScriptSerializer().Serialize(settings));
                logPath=Path.Combine(artifacts,"installer.log");
                using(var log=new StreamWriter(logPath,true)) {
                    log.AutoFlush=true; object gate=new object();
                    var start=new ProcessStartInfo("powershell.exe","-NoProfile -ExecutionPolicy Bypass -File \""+Path.Combine(installRoot,"release","gui","install.ps1")+"\" -OptionsFile \""+choices+"\"") {
                        UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true,WorkingDirectory=installRoot
                    };
                    using(var process=new Process{StartInfo=start}) {
                        DataReceivedEventHandler capture=delegate(object sender,DataReceivedEventArgs e) { if(e.Data!=null) { lock(gate)log.WriteLine(e.Data); Line(e.Data); } };
                        process.OutputDataReceived+=capture; process.ErrorDataReceived+=capture;
                        process.Start(); process.BeginOutputReadLine(); process.BeginErrorReadLine(); process.WaitForExit();
                        if(process.ExitCode!=0) throw new Exception("Setup stopped (code "+process.ExitCode+"). Read the log below, correct the problem and choose Retry. Your partial installation has been kept.");
                    }
                }
            });
            installed=true; heading.Text="Ready for deployment."; status.Text="Sierra Romeo is installed.";
            explanation.Text="Your local build is ready. Launch the game now, or use your shortcut later.\nYour saves will be stored in userdata/player inside the installation folder.";
            if(warnings) explanation.Text="The game is ready, but a shortcut could not be created. See the log below.\nYou can launch here or open Play.cmd in the installation folder.";
            next.Text="Launch"; progress.Style=ProgressBarStyle.Blocks; progress.Value=100;
        } catch(Exception ex) {
            heading.Text="Setup needs your attention."; status.Text="Installation did not finish.";
            explanation.Text=ex.Message; next.Text="Retry"; progress.Style=ProgressBarStyle.Blocks; progress.Value=0;
            back.Visible=true;
            Line("ERROR: "+ex.ToString());
        } finally { running=false; next.Enabled=true; close.Enabled=true; close.Text="Close"; }
    }
    void Launch() {
        try { Process.Start(new ProcessStartInfo("powershell.exe","-NoProfile -ExecutionPolicy Bypass -File \""+Path.Combine(installRoot,"tools","run.ps1")+"\""){WorkingDirectory=installRoot,UseShellExecute=false,CreateNoWindow=true}); Close(); }
        catch(Exception ex) { MessageBox.Show(this,ex.Message,"Could not launch game"); }
    }
}
