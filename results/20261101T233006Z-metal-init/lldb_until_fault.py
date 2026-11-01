import lldb
import signal


def record(debugger):
    debugger.HandleCommand("process status")
    debugger.HandleCommand("thread backtrace")
    debugger.HandleCommand("register read pc")
    debugger.HandleCommand("image lookup --address $pc")
    debugger.HandleCommand("thread info")


def faulting(thread):
    reason = thread.GetStopReason()
    if reason in (lldb.eStopReasonException, lldb.eStopReasonBreakpoint, lldb.eStopReasonWatchpoint):
        return True
    if reason == lldb.eStopReasonSignal and thread.GetStopReasonDataCount():
        return thread.GetStopReasonDataAtIndex(0) != signal.SIGSTOP
    return False


def until_fault(debugger, command, result, internal_dict):
    proc = debugger.GetSelectedTarget().GetProcess()
    for _ in range(400):
        if proc.GetState() == lldb.eStateExited:
            break
        thread = proc.GetSelectedThread()
        if proc.GetState() == lldb.eStateStopped and thread.IsValid() and faulting(thread):
            break
        error = proc.Continue()
        if not error.Success() and proc.GetState() != lldb.eStateExited:
            result.AppendMessage("continue failed: %s" % error.GetCString())
            return
    if proc.GetState() == lldb.eStateExited:
        result.AppendMessage(
            "Process %d exited with status = %d" % (proc.GetProcessID(), proc.GetExitStatus())
        )
        return
    if proc.GetState() != lldb.eStateStopped or not faulting(proc.GetSelectedThread()):
        result.AppendMessage("no fault stop")
        return
    record(debugger)
    proc.Continue()
    if proc.GetState() == lldb.eStateExited:
        result.AppendMessage(
            "Process %d exited with status = %d" % (proc.GetProcessID(), proc.GetExitStatus())
        )
        return
    record(debugger)
    proc.Continue()
    if proc.GetState() == lldb.eStateExited:
        result.AppendMessage(
            "Process %d exited with status = %d" % (proc.GetProcessID(), proc.GetExitStatus())
        )


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand("command script add -f lldb_until_fault.until_fault until_fault")
