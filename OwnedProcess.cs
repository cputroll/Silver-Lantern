using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Threading;
using System.Runtime.InteropServices;
using System.Text;

public static class OwnedProcess {
    const uint EXTENDED_STARTUPINFO_PRESENT = 0x00080000, CREATE_SUSPENDED = 4;
    const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000, INFINITE_ERROR = 0xffffffff;
    [StructLayout(LayoutKind.Sequential)] struct SECURITY_ATTRIBUTES { public int length; public IntPtr securityDescriptor; [MarshalAs(UnmanagedType.Bool)] public bool inheritHandle; }
    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern IntPtr CreateFile(string name, uint access, uint share, ref SECURITY_ATTRIBUTES security, uint creation, uint flags, IntPtr template);
    [StructLayout(LayoutKind.Sequential)] struct STARTUPINFO { public int cb; public IntPtr reserved, desktop, title; public int x,y,xsize,ysize,xchars,ychars,fill,flags; public short show,reserved2; public IntPtr reservedPtr, input, output, error; }
    [StructLayout(LayoutKind.Sequential)] struct STARTUPINFOEX { public STARTUPINFO startup; public IntPtr attributes; }
    [StructLayout(LayoutKind.Sequential)] struct PROCESS_INFORMATION { public IntPtr process, thread; public int pid, tid; }
    [StructLayout(LayoutKind.Sequential)] struct BASIC_LIMIT { public long perProcess, perJob; public uint flags; public UIntPtr minWorking, maxWorking; public uint active; public UIntPtr affinity; public uint priority, scheduling; }
    [StructLayout(LayoutKind.Sequential)] struct IO_COUNTERS { public ulong readOps, writeOps, otherOps, readBytes, writeBytes, otherBytes; }
    [StructLayout(LayoutKind.Sequential)] struct EXTENDED_LIMIT { public BASIC_LIMIT basic; public IO_COUNTERS io; public UIntPtr processMemory, jobMemory, peakProcess, peakJob; }
    [StructLayout(LayoutKind.Sequential)] struct BASIC_ACCOUNTING { public long totalUser, totalKernel, periodUser, periodKernel; public uint pageFaults, totalProcesses, activeProcesses, terminatedProcesses; }
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr job, int kind, out BASIC_ACCOUNTING info, uint length, IntPtr returned);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateJobObject(IntPtr job, uint exitCode);
    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr security, string name);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job, int kind, ref EXTENDED_LIMIT info, uint length);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref IntPtr size);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
    [DllImport("kernel32.dll")] static extern void DeleteProcThreadAttributeList(IntPtr list);
    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern bool CreateProcess(string app, StringBuilder command, IntPtr processSecurity, IntPtr threadSecurity, bool inherit, uint flags, IntPtr environment, string directory, ref STARTUPINFOEX startup, out PROCESS_INFORMATION process);
    [DllImport("kernel32.dll", SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError=true)] static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int kind);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool DuplicateHandle(IntPtr sourceProcess, IntPtr source, IntPtr targetProcess, out IntPtr target, uint access, bool inherit, uint options);
    static Exception Error(string operation) { return new Win32Exception(Marshal.GetLastWin32Error(), operation); }
    static string Quote(string value) {
        StringBuilder result = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            if (c == '\"') { result.Append('\\', slashes * 2 + 1); result.Append(c); }
            else { result.Append('\\', slashes); result.Append(c); }
            slashes = 0;
        }
        result.Append('\\', slashes * 2); return result.Append('\"').ToString();
    }
    static IntPtr DuplicateOutput(int kind, IntPtr fallback) {
        IntPtr source = GetStdHandle(kind), result;
        if (source == IntPtr.Zero || source == new IntPtr(-1)) source = fallback;
        IntPtr process = GetCurrentProcess();
        if (!DuplicateHandle(process, source, process, out result, 0, true, 2)) throw Error("DuplicateHandle standard output");
        return result;
    }
    static bool WaitForEmptyJob(IntPtr job, Stopwatch clock, long deadline) {
        for (;;) {
            BASIC_ACCOUNTING accounting;
            if (!QueryInformationJobObject(job, 1, out accounting, (uint)Marshal.SizeOf(typeof(BASIC_ACCOUNTING)), IntPtr.Zero)) throw Error("QueryInformationJobObject");
            if (accounting.activeProcesses == 0) return clock.ElapsedMilliseconds <= deadline;
            long remaining = deadline - clock.ElapsedMilliseconds;
            if (remaining <= 0) return false;
            Thread.Sleep((int)Math.Min(remaining, 25));
        }
    }
    public static int Run(string executable, string[] args, int timeoutMilliseconds) {
        if (timeoutMilliseconds <= 0) throw new ArgumentOutOfRangeException("timeoutMilliseconds");
        IntPtr job = IntPtr.Zero, attrs = IntPtr.Zero, jobArray = IntPtr.Zero, handleArray = IntPtr.Zero, nullHandle = IntPtr.Zero, outputHandle = IntPtr.Zero, errorHandle = IntPtr.Zero; bool initialized = false;
        PROCESS_INFORMATION pi = new PROCESS_INFORMATION();
        Stopwatch clock = Stopwatch.StartNew(); bool verifiedEmpty = false;
        try {
            job = CreateJobObject(IntPtr.Zero, null); if (job == IntPtr.Zero) throw Error("CreateJobObject");
            EXTENDED_LIMIT limits = new EXTENDED_LIMIT(); limits.basic.flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if (!SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(typeof(EXTENDED_LIMIT)))) throw Error("SetInformationJobObject");
            SECURITY_ATTRIBUTES security = new SECURITY_ATTRIBUTES(); security.length = Marshal.SizeOf(typeof(SECURITY_ATTRIBUTES)); security.inheritHandle = true;
            nullHandle = CreateFile("NUL", 0xc0000000, 3, ref security, 3, 0, IntPtr.Zero);
            if (nullHandle == new IntPtr(-1)) { nullHandle = IntPtr.Zero; throw Error("CreateFile NUL"); }
            IntPtr size = IntPtr.Zero; InitializeProcThreadAttributeList(IntPtr.Zero, 2, 0, ref size);
            if (size == IntPtr.Zero) throw Error("InitializeProcThreadAttributeList size");
            attrs = Marshal.AllocHGlobal(size);
            if (!InitializeProcThreadAttributeList(attrs, 2, 0, ref size)) throw Error("InitializeProcThreadAttributeList");
            initialized = true; jobArray = Marshal.AllocHGlobal(IntPtr.Size); Marshal.WriteIntPtr(jobArray, job);
            if (!UpdateProcThreadAttribute(attrs, 0, (IntPtr)0x0002000d, jobArray, (IntPtr)IntPtr.Size, IntPtr.Zero, IntPtr.Zero)) throw Error("UpdateProcThreadAttribute JOB_LIST");
            outputHandle = DuplicateOutput(-11, nullHandle); errorHandle = DuplicateOutput(-12, nullHandle);
            handleArray = Marshal.AllocHGlobal(IntPtr.Size * 3); Marshal.WriteIntPtr(handleArray, 0, nullHandle); Marshal.WriteIntPtr(handleArray, IntPtr.Size, outputHandle); Marshal.WriteIntPtr(handleArray, IntPtr.Size * 2, errorHandle);
            if (!UpdateProcThreadAttribute(attrs, 0, (IntPtr)0x00020002, handleArray, (IntPtr)(IntPtr.Size * 3), IntPtr.Zero, IntPtr.Zero)) throw Error("UpdateProcThreadAttribute HANDLE_LIST");
            STARTUPINFOEX startup = new STARTUPINFOEX(); startup.startup.cb = Marshal.SizeOf(typeof(STARTUPINFOEX)); startup.attributes = attrs;
            startup.startup.flags = 0x100; startup.startup.input = nullHandle; startup.startup.output = outputHandle; startup.startup.error = errorHandle;
            StringBuilder command = new StringBuilder(Quote(executable)); foreach (string arg in args) command.Append(' ').Append(Quote(arg));
            if (!CreateProcess(executable, command, IntPtr.Zero, IntPtr.Zero, true, EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED, IntPtr.Zero, null, ref startup, out pi)) throw Error("CreateProcess with JOB_LIST");
            if (ResumeThread(pi.thread) == INFINITE_ERROR) throw Error("ResumeThread");
            long remaining = Math.Max(0, timeoutMilliseconds - clock.ElapsedMilliseconds);
            uint wait = WaitForSingleObject(pi.process, (uint)remaining);
            if (wait == 0x102) throw new TimeoutException("Owned process timed out");
            if (wait != 0) throw Error("WaitForSingleObject");
            uint exitCode; if (!GetExitCodeProcess(pi.process, out exitCode)) throw Error("GetExitCodeProcess");
            if (!WaitForEmptyJob(job, clock, timeoutMilliseconds)) throw new TimeoutException("Owned process job timed out");
            verifiedEmpty = true;
            return unchecked((int)exitCode);
        } catch (Exception failure) {
            if (job != IntPtr.Zero && pi.process != IntPtr.Zero && !verifiedEmpty) {
                try {
                    if (!TerminateJobObject(job, 1)) throw Error("TerminateJobObject");
                    Stopwatch teardown = Stopwatch.StartNew();
                    if (!WaitForEmptyJob(job, teardown, 10000)) throw new TimeoutException("Owned process teardown could not verify an empty job");
                } catch (Exception teardownFailure) {
                    throw new AggregateException("Owned process failure; teardown could not be verified", failure, teardownFailure);
                }
            }
            throw;
        } finally {
            if (job != IntPtr.Zero) CloseHandle(job);
            if (pi.thread != IntPtr.Zero) CloseHandle(pi.thread);
            if (pi.process != IntPtr.Zero) CloseHandle(pi.process);
            if (initialized) DeleteProcThreadAttributeList(attrs);
            if (attrs != IntPtr.Zero) Marshal.FreeHGlobal(attrs);
            if (jobArray != IntPtr.Zero) Marshal.FreeHGlobal(jobArray);
            if (handleArray != IntPtr.Zero) Marshal.FreeHGlobal(handleArray);
            if (outputHandle != IntPtr.Zero) CloseHandle(outputHandle);
            if (errorHandle != IntPtr.Zero) CloseHandle(errorHandle);
            if (nullHandle != IntPtr.Zero) CloseHandle(nullHandle);
        }
    }
}
