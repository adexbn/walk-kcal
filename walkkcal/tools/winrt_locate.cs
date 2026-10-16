// -*- coding: utf-8 -*-
// winrt_locate.cs —— Windows 定位探针（WiFi 指纹）
//
// 由 walkkcal/locate.py 用系统自带 csc.exe 编译后调用，输出一行 JSON。
//
// ★ 为什么这么写（踩过的坑，2026-10-10 实测六代才通）：
//   1. 桌面上**不能**用 `[Windows.Devices.Geolocation.Geolocator, ...]` 这种
//      类型字面量 —— .NET Framework 的 csc.exe 不认 `ContentType=WindowsRuntime`。
//      必须 `Type.GetType("... , Windows, ContentType=WindowsRuntime")`。
//   2. WinRT 的 IAsyncOperation 要用 `System.Runtime.WindowsRuntime.dll` 里的
//      `System.WindowsRuntimeSystemExtensions.AsTask<T>()` 转成 Task 才能 Wait。
//      该程序集在 .NET Framework 目录下，用 Assembly.LoadFrom 反射加载。
//   3. 直接 `Add-Type` / 强类型引用在 PowerShell 7 下不可用（PS7 无 WinRT 投影）。
//   4. **不要调 RequestAccessAsync** —— 桌面无 UI 时会一直等授权弹窗，
//      表现为"挂起"。直接 GetGeopositionAsync 即可，有权限就返回坐标。
//
// 输出 JSON 字段：
//   ok              是否拿到可用坐标
//   lat / lon       坐标
//   accuracy_m      自报精度（米）
//   source          PositionSource：WiFi / Satellite / IPAddress / Unknown / Default
//   remote          IsRemoteSource（true = 远端源，通常是代理/机房导致的）
//   location_status LocationStatus：NotInitialized / Disabled / Denied / Allowed
//   error           失败原因

using System;
using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;

public class WinRtLocate
{
    [DllImport("api-ms-win-core-winrt-l1-1-0.dll")]
    static extern int RoInitialize(int type);

    static Assembly AsmWinRT;
    static MethodInfo AsTaskGeneric;

    static object AwaitTask(object asyncOp, Type resultType, int timeoutMs, out string err)
    {
        err = null;
        try
        {
            var mi = AsTaskGeneric.MakeGenericMethod(resultType);
            var task = (Task)mi.Invoke(null, new object[] { asyncOp });
            if (!task.Wait(timeoutMs)) { err = "timeout(" + timeoutMs + "ms)"; return null; }
            if (task.IsFaulted)
            {
                var ae = task.Exception;
                err = "faulted:" + (ae != null ? ae.GetBaseException().Message : "?");
                return null;
            }
            return task.GetType().GetProperty("Result").GetValue(task, null);
        }
        catch (Exception e)
        {
            err = e.GetType().Name + ":" + e.Message;
            return null;
        }
    }

    static string J(string s)
    {
        if (s == null) return "null";
        return "\"" + s.Replace("\\", "\\\\").Replace("\"", "\\\"")
                       .Replace("\r", " ").Replace("\n", " ") + "\"";
    }

    static string Num(double d)
    {
        return d.ToString("F6", CultureInfo.InvariantCulture);
    }

    public static int Main(string[] args)
    {
        int timeoutMs = 8000;
        if (args.Length > 0)
        {
            int t;
            if (int.TryParse(args[0], out t) && t > 0) timeoutMs = t;
        }

        string result;
        try { result = Run(timeoutMs); }
        catch (Exception e) { result = Fail(e.GetType().Name + ":" + e.Message, null); }

        var stdout = Console.OpenStandardOutput();
        var bytes = new UTF8Encoding(false).GetBytes(result + "\n");
        stdout.Write(bytes, 0, bytes.Length);
        stdout.Flush();
        return 0;
    }

    static string Run(int timeoutMs)
    {
        // WinRT 需要先初始化公寓（失败不致命，GetActivationFactory 会自己处理）
        try { RoInitialize(1); } catch { }

        try
        {
            string fw = System.IO.Path.GetDirectoryName(typeof(object).Assembly.Location);
            AsmWinRT = Assembly.LoadFrom(System.IO.Path.Combine(fw, "System.Runtime.WindowsRuntime.dll"));
            var ext = AsmWinRT.GetType("System.WindowsRuntimeSystemExtensions");
            if (ext == null) return Fail("no-WindowsRuntimeSystemExtensions", null);
            foreach (var m in ext.GetMethods())
                if (m.Name == "AsTask" && m.GetParameters().Length == 1 &&
                    m.GetParameters()[0].ParameterType.Name.StartsWith("IAsyncOperation`1"))
                { AsTaskGeneric = m; break; }
        }
        catch (Exception e) { return Fail("load-failed:" + e.Message, null); }

        if (AsTaskGeneric == null) return Fail("no-AsTask", null);

        Type geoType = Type.GetType(
            "Windows.Devices.Geolocation.Geolocator, Windows, ContentType=WindowsRuntime");
        Type posType = Type.GetType(
            "Windows.Devices.Geolocation.Geoposition, Windows, ContentType=WindowsRuntime");
        if (geoType == null || posType == null) return Fail("no-winrt-geolocation-type", null);

        object g = Activator.CreateInstance(geoType);

        string status = Str(SafeProp(geoType, g, "LocationStatus"));

        // ★ 不调 RequestAccessAsync（无 UI 会挂住），直接取位置
        string err;
        var mGet = geoType.GetMethod("GetGeopositionAsync", new Type[0]);
        if (mGet == null) return Fail("no-GetGeopositionAsync", status);
        object pos = AwaitTask(mGet.Invoke(g, null), posType, timeoutMs, out err);
        if (pos == null) return Fail(err, status);

        object coord = posType.GetProperty("Coordinate").GetValue(pos, null);
        if (coord == null) return Fail("no-coordinate", status);
        Type ct = coord.GetType();

        double lat, lon;
        try
        {
            lat = Convert.ToDouble(SafeProp(ct, coord, "Latitude"), CultureInfo.InvariantCulture);
            lon = Convert.ToDouble(SafeProp(ct, coord, "Longitude"), CultureInfo.InvariantCulture);
        }
        catch (Exception e) { return Fail("parse-latlon:" + e.Message, status); }

        object accO = SafeProp(ct, coord, "Accuracy");
        string src = Str(SafeProp(ct, coord, "PositionSource"));
        object remO = SafeProp(ct, coord, "IsRemoteSource");
        bool remote = (remO is bool) && (bool)remO;

        string accStr = "null";
        if (accO != null)
        {
            try { accStr = Math.Round(Convert.ToDouble(accO, CultureInfo.InvariantCulture)).ToString("F0", CultureInfo.InvariantCulture); }
            catch { }
        }

        return "{\"ok\":true,\"lat\":" + Num(lat) + ",\"lon\":" + Num(lon) +
               ",\"accuracy_m\":" + accStr + ",\"source\":" + J(src) +
               ",\"remote\":" + (remote ? "true" : "false") +
               ",\"location_status\":" + J(status) + "}";
    }

    static object SafeProp(Type t, object o, string name)
    {
        try { var p = t.GetProperty(name); return p == null ? null : p.GetValue(o, null); }
        catch { return null; }
    }

    static string Str(object o) { return o == null ? null : Convert.ToString(o); }

    static string Fail(string err, string status)
    {
        return "{\"ok\":false,\"error\":" + J(err) +
               ",\"location_status\":" + J(status) + "}";
    }
}
