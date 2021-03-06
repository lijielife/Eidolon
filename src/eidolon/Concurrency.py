# Eidolon Biomedical Framework
# Copyright (C) 2016-8 Eric Kerfoot, King's College London, all rights reserved
# 
# This file is part of Eidolon.
#
# Eidolon is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# Eidolon is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License along
# with this program (LICENSE.txt).  If not, see <http://www.gnu.org/licenses/>

'''
This module contains all the routines and types for implementing multiprocessor concurrency. A ProcessServer object must
be created at initialization time (specifically before the UI is instantiated) using ProcessServer.CreateGlobalServer().
Any concurrent jobs must be run through this object which the `concurrent' function decorator does. This module uses pure
Python and so should be portable.

A routine executed through the ProcessServer is executed on each of the requested processes and is passed a different
instance of AlgorithmProcess in the first argument to provide information abou which process is being used. A routine
is executed in this way through ProcessServer.callProcessFunc() or by using the @concurrent decorator, see the docs for
these for further explanation. 

This module is use by algorithms in various placed in Eidolon to implement concurrency. Multiprocessing is necessary to
get around the limitations of the GIL in Python.
'''


import os
import atexit
import gc
import time
import errno
import threading
import traceback
import functools
import types
import inspect
import marshal
from multiprocessing import Pipe, Process, cpu_count, Array, Value, Lock, Event

try:
    from .Utils import queue, lockobj, printFlush, processExists, Task, clamp, Future, partitionSequence, listSum
except ImportError:
    from Utils import queue, lockobj, printFlush, processExists, Task, clamp, Future, partitionSequence, listSum

class MethodProxy(object):
    '''
    A proxy type for a method of an object hosted remotely. When called, a message containing the name of the
    method and the given arguments is sent down the send Connection object, and the result is collected from the
    recv object. This assumes that all arguments and return values are picklable and that a server object is present
    at the receiver's end to receive the method message and execute response behaviour.
    '''
    def __init__(self,name,send,recv):
        self.name=name
        self.send=send
        self.recv=recv

    def __call__(self,*args,**kwargs):
        self.send.send((self.name,args,kwargs))
        if self.recv.poll(10):
            result=self.recv.recv()
            if isinstance(result,Exception):
                raise result
            return result
        else:
            raise IOError('Did not get result back for method call '+self.name)


class DynamicProxy(object):
    '''
    This type uses MethodProxy to generate methods for itself to proxy the given object. Each method in 'obj' whose name
    does not start with _ is used to generate a method for the proxy object, thus the proxy has almost the same signature
    as the original object. This proxy object can be pickled and sent to concurrent clients (threads or processes).
    '''
    def __init__(self,obj):
        self.send,self.send_r=Pipe()
        self.recv_s,self.recv=Pipe()

        for i in dir(obj):
            if i[0]!='_' and hasattr(getattr(obj,i),'__call__'):
                setattr(self,i,MethodProxy(i,self.send,self.recv))


class ObjectServer(object):
    '''
    ObjectServer creates picklable proxies for given objects and instantiates threads to operate the proxies' interactions.
    This allows objects to be proxied to separate processes which can call these objects' methods and interact with them using
    other picklable objects. Proxies must not be shared by processes and it is the proxied object's responsibility to ensure
    concurrent safety. This class uses DynamicProxy and MethodProxy to implement this behaviour.
    '''
    def __init__(self):
        self.objmap={}
        self.doRun=True
        self.threads=[]

    def getProxy(self,obj):
        proxy=DynamicProxy(obj)
        t=threading.Thread(target=self.runProxy,args=(obj,proxy))
        t.start()
        self.threads.append(t)

        return proxy

    def __enter__(self):
        self.doRun=True
        return self

    def __exit__(self,exc_type, exc_value, tb):
        self.stop()

    def stop(self):
        self.doRun=False
        for t in self.threads:
            t.join()

    def reset(self):
        self.stop()
        self.objmap.clear()
        self.threads=[]
        self.doRun=True

    def runProxy(self,obj,proxy):
        while self.doRun:
            try:
                if proxy.send_r.poll(0.01):
                    with lockobj(obj):
                        try:
                            name,args,kwargs=proxy.send_r.recv()
                            result=getattr(obj,name)(*args,**kwargs)
                        except Exception as e:
                            result=e

                        proxy.recv_s.send(result)
            except IOError as e:
                if e.errno!=errno.EINTR:
                    raise


class ObjectSharer(object):
    '''
    This type wraps a map of (index,name) pairs to objects, where index can be used to be a process index number.
    When this object is shared to multiple processes using ObjectServer, it can be used to share picklable objects
    amongst the processes. A process stored an object giving its index and the object name for the (index,name) pair
    and can then ask for all the object of a given name from the other processes using 'getObjects()'.
    '''
    def __init__(self):
        self.objmap={}

    def shareObject(self,index,name,obj):
        self.objmap[(index,name)]=obj

    def getObjects(self,name,excludeIndex=-1):
        return dict((i,o) for (i,n),o in self.objmap.items() if n==name and i!=excludeIndex)

    def clear(self):
        self.objmap.clear()


class AlgorithmProcess(Process):
    '''
    This object represents a separate process as well as the mechanisms for sharing objects between them and
    synchronization. It is used by 'concurrentAlgorithm' and 'concurrent', and should not be instantiated separately.
    '''
    def __init__(self,index,total,syncEvent,syncEvent2,syncCounter,syncLock,sharer,progress,stopEvent,parentPID):
        Process.__init__(self)
        self.index=index # which proc this is, 0<=index<total
        self.total=total # how many procs there are total
        self.syncEvent=syncEvent # synchronizaing event procs wait on in sync()
        self.syncEvent2=syncEvent2
        self.syncCounter=syncCounter # synchronizing counter value shared between procs
        self.syncLock=syncLock # shared lock object controlling access to syncCounter
        self.sharer=sharer # object sharer shared amongst all procs
        self.progress=progress # progress indicator, either a Task object or a shared Python int list
        self.stopEvent=stopEvent # Event object shared amongst procs, should exit if set
        self.parentPID=parentPID # process ID of the parent proc, should exit if this proc dies

        self.startval=0 # starting value for this proc's range of values
        self.endval=0 # ending value for this proc's range of values
        self.maxval=0 # maximal range value, is more than endval for every proc but the last

        self.send,self.recv=Pipe() # Connection objects for sending jobs to this proc
        self.rsend,self.rrecv=Pipe() # Connection objects for sending results back to parent

        self.progressTime=time.time()

    def __repr__(self):
        return '<AlgorithmProcess %xi, Index: %i / %i, Start: %i, End: %i>'%(id(self),self.index,self.total,self.startval,self.endval)

    def shareObject(self,name,obj, doExchange=True):
        '''
        Share the value 'obj' under the name 'name', returning the objects of the same name from other processes
        if 'doExchange' is true. The assumption is that all procs share objects of the same name at once and so this
        method will call sync(). It will not return the object for the current process.
        '''
        if self.total==1 or not self.sharer:
            return {}
        else:
            self.sharer.shareObject(self.index,name,obj)

            if doExchange:
                self.sync()
                return self.getObjects(name)

    def getObjects(self,name):
        '''
        Returns a dict mapping process IDs to the shared objects of the given name, excluding the current process' object.
        This will return {} if no shared objects of the given name isn't present or if the sharing object isn't present.
        '''
        if self.sharer:
            return self.sharer.getObjects(name,self.index)
        else:
            return {}

    def sync(self):
        '''
        Causes the calling process to block until all others have also called this method. This synchronizes between multiple
        processes by incrementing the shared counter value `syncCounter'. Each process calls 'sync()' and is forced to wait
        until all other processes have done the same. This essentially functions as a concurrent checkpointing mechanism.
        If one process throws an exception then `syncCounter' is set to a negative value to indicate this, causing any proc
        which calls sync() in this case to throw an exception as well. If self.syncEvent is None then this method exits
        without doing anything, allowing it to be safely called in single-process operation. All processes MUST call
        this method at the same point in code otherwise deadlock will result.
        '''
        if self.syncEvent!=None:
            with self.syncLock:
                if self.syncCounter.value<0: # if another process has thrown an exception, don't attempt to sync
                    dowait=False
                else:
                    if self.syncCounter.value==0: # first process to call sync() clears the event
                        self.syncEvent.clear() # reset the block
    
                    self.syncCounter.value+=1
                    dowait=self.syncCounter.value<self.total
                    if not dowait: # only true when the calling process is the last to call sync()
                        self.syncEvent.set() # tell all the other processes to continue
                        self.syncCounter.value=0

            # wait() must be called outside the 'with' block otherwise everyone will deadlock
            # dowait is true when the calling process is not the last to call sync()
            while dowait and self._continueRunning():
                dowait=not self.syncEvent.wait(0.01) and self.syncCounter.value>=0
            
            # Swap sync events so that the process doesn't attempt to resync using the same event. 
            # If this did occur, other processes still waiting in the above loop for self.syncEvent.wait() to let 
            # them go will wait forever because self.syncEvent.clear() will get called beforehand, causing deadlock. 
            self.syncEvent2,self.syncEvent=self.syncEvent,self.syncEvent2
            
            if self.syncCounter.value<0:
                raise Exception('Sibling process encountered exception')

            if not self._continueRunning():
                raise Exception('Parent exited before child completed processing')

    def nrange(self):
        '''Returns an iterator from `self.startval' to `self.endval'-1.'''
        return range(self.startval,self.endval)

    def prange(self):
        '''Yield each number from self.startval to self.endval-1 and update progress by calling setProgress().'''
        count=0
        for i in self.nrange():
            count+=1
            self.setProgress(count)
            yield i

        self.setProgress(count,True)

    def setProgress(self,val,forceUpdate=False):
        '''
        Sets the progress indicator value of the associated Task object or the shared value array, only updates if
        the previous update was more than 200ms in the past.
        '''

        curtime=time.time()
        if (curtime-self.progressTime)<0.2 and not forceUpdate:
            return

        self.progressTime=curtime
        if isinstance(self.progress,Task):
            self.progress.setProgress(val)
        elif self.progress:
            self.progress[self.index]=val

    def _continueRunning(self):
        '''Returns True so long as the `stopEvent' is not set and the parent process has not exited.'''
        return not self.stopEvent.is_set() and processExists(self.parentPID)

    def run(self):
        '''
        Executes operations by unpacking instruction tuples from the receiving pipe and calling the appropriate
        function and then returning the results (or an exception) back through the sending pipe.
        '''
        while self._continueRunning():
            if self.recv.poll(0.01):
                try:
                    target,args,kwargs,self.startval,self.endval,self.maxval,self.total=self.recv.recv()
                    result=target(self,*args,**kwargs)
                    self.rsend.send(result)
                except Exception as e:
                    printFlush('PROC',self.index,e)
                    traceback.print_exc()
                    self.rsend.send(e)
                    with self.syncLock:
                        self.syncCounter.value=-self.total-1 # indicate exceptional conditions

                gc.collect()


class ProcessServer(threading.Thread):
    '''
    This type manages the creation of subprocesses and the despatch of computational tasks to them. Typically the global
    instance is created at startup through createGlobalServer() at which point the subprocesses are created. Tasks are
    enqueued to be executed through callProcessFunc() or indirectly if a routine decorated with @concurrent is called.
    
    '''

    globalServer=None # global instance of the server

    @staticmethod
    def createGlobalServer(realnumprocs=cpu_count()):
        '''Creates the global instance of the server, `realnumprocs' being the number of processes to create.'''
        ProcessServer.globalServer=ProcessServer(realnumprocs)
        ProcessServer.globalServer.start()

    def __init__(self,realnumprocs=cpu_count()):
        threading.Thread.__init__(self)
        self.daemon=True

        self.realnumprocs=clamp(realnumprocs,1,cpu_count())
        self.procs=[]
        self.sharer=ObjectSharer()
        self.syncEvent=Event()
        self.syncEvent2=Event()
        self.syncCounter=Value('i',0)
        self.syncLock=Lock()
        self.jobqueue=queue.Queue()
        self.progress=Array('l',self.realnumprocs)
        self.objsrv=ObjectServer()
        self.stopEvent=Event()

    def callProcessFunc(self,valrange,numprocs,task,target,*args,**kwargs):
        '''
        Sends the request to execute `target' in parallel with the given `args' and `kwargs' argument values. The 
        `valrange' value is used to set the value range members of the AlgorithmProcess objects, `numprocs' is the 
        number of processes to execute `target' on (1 will cause `target' to be executed in the main process, a value 
        <=0 will mean all the processes will be used), and `task' is the (possibly None) Task object to report 
        progress to. The return value is a Future object which will contain the results or exceptions thrown.
        
        The `target' routine must exist in a module scope at runtime so inner routines or dynamically created routines
        cannot be used here. When called, the first argument will be the AlgorithmProcess object followed by those in
        `args' and `kwargs'. The AlgorithmProcess instance will be different for each process `target' is called in,
        its members will state which process and the subset of `valrange' assigned to it. The expectation is that
        `valrange' is the number of elements `target' is used to operate on, when called each process is assigned a 
        subset of the range [0,`valrange') and the internal algorithm of `target' is expected to iterate over those 
        values only. 
        
        The optional argument "partitionArgs" in `kwargs' may contain a tuple containing the members in `args' which
        are iterable and which should be partitioned amongst the processes. Values in `args' and `kwargs' are normally
        copied to each process, so this allows large iterables to be partitioned and not needlessly duplicated.
        
        The @concurrent can be used to wrap the invocation of callProcessFunc() within the function definition itself.
        The first argument of the routine must still be the AlgorithmProcess object but when called three arguments
        representing `valrange', `numprocs', and `task' must be provided instead. The routine will also no block until
        the processing is complete and return the results instead of a Future object.
        
        Example:
            def testfunc(process):
                return (process.index,int(process.startval),int(process.endval))
            
            result=ProcessServer.globalServer.callProcessFunc(50,4,None,testfunc)
            printFlush(listResults(result()))
        
        Output: [(0, 0, 12), (1, 12, 25), (2, 25, 37), (3, 37, 50)]
        
        Example:
            @concurrent 
            def testfunc(process,values):
                return (process.index,values)
            
            values=range(20)
            result=testfunc(len(values),3,None,values,partitionArgs=(values,))
            printFlush(listResults(result))
            
        Output: [(0, [0, 1, 2]), (1, [3, 4, 5]), (2, [6, 7, 8, 9])]
        '''
        result=Future()
        self.jobqueue.put((valrange,numprocs,task,target,args,kwargs,result))
        return result

    def prepareArgs(self,index,numprocs,args,partArgs):
        '''Prepare arguments by dividing lists/tuples present in both `args' and `partArgs' into the slice for proc `index'.'''
        pargs=[]
        for a in args: # construct an argument list `pargs' to be passed to the process object
            if a in partArgs:
                astart,aend=partitionSequence(len(a),index,numprocs)
                pargs.append(a[astart:aend]) # replace `a' with a per-process slice of `a'
            else:
                pargs.append(a) # use `a' directly

        return pargs

    def run(self):
        '''Run the server, reading messages from the job queue and sending them to the work processes.'''
        atexit.register(self.stop)

        # do not create processes if the number of procs is 1, this forces single process mode
        if self.realnumprocs>1:
            # start all the processes
            for i in range(self.realnumprocs):
                psharer=self.objsrv.getProxy(self.sharer)
                p=AlgorithmProcess(i,self.realnumprocs,self.syncEvent,self.syncEvent2,self.syncCounter,self.syncLock,psharer,self.progress,self.stopEvent,os.getpid())
                p.start()
                self.procs.append(p)

        # continually read items from `self.jobqueue' and send the execution requests to the processes
        while not self.stopEvent.is_set():
            valrange,numprocs,task,target,args,kwargs,result=self.jobqueue.get(True) # get message
            
            partArgs=kwargs.pop('partitionArgs',()) # get list of objects to partition between processes
            numprocs=min(valrange,self.realnumprocs if numprocs<=0 or numprocs>self.realnumprocs else numprocs) # get number of processes to use,

            self.sharer.clear()
            
            for i in range(self.realnumprocs): # reset the progress counting shared array
                self.progress[i]=0

            if task: # set the task's progress value
                task.setMaxProgress(valrange)

            with result:
                if numprocs==1 or not self.procs: # if we are to use only one process execute locally instead of 1 concurrent process
                    try:
                        # construct a local process, passing None for parameters clues it in to not try using concurrency features like syncing
                        localproc=AlgorithmProcess(0,1,None,None,None,None,None,task,None,0)
                        localproc.endval=valrange
                        localproc.maxval=valrange
                                
                        tresult=target(localproc,*args,**kwargs)
                        result.setObject({0:tresult})
                    except Exception as e:
                        printFlush('LOCALPROC',e)
                        traceback.print_exc()
                        result.setObject({0:e})

                else: # otherwise use work processes
                    self.syncCounter.value=0 # reset the sync counter, the processes can't do this themselves cleanly without race condition

                    # for each process prepare the arguments to the target and send the request through its `send' pipe
                    for i in range(numprocs):
                        start,end=partitionSequence(valrange,i,numprocs)
                        pargs=self.prepareArgs(i,numprocs,args,partArgs)

                        self.procs[i].send.send((target,pargs,kwargs,start,end,valrange,numprocs)) # send the job

                    try:
                        # wait for each process being used to finish, updating the task object's progress if it's present
                        while any(not p.rrecv.poll(0.01) for p in self.procs[:numprocs]): # a process is done when it has sent its result
                            if task:
                                task.setProgress(sum(self.progress))

                        if task: # do a final update
                            task.setProgress(sum(self.progress))

                        result.setObject(dict((p.index,p.rrecv.recv()) for p in self.procs[:numprocs])) # map results to process index
                    except Exception as e:
                        # some error occurred, send e as the result for each process even though it wasn't actually thrown by the processes
                        result.setObject(dict((p.index,e) for p in self.procs[:numprocs]))

    def stop(self):
        '''Stops the processes and object server, no execution after this is possible.'''
        self.objsrv.stop()
        self.stopEvent.set()


def chooseProcCount(numelems,refine,threshold):
    '''
    Determine a process count to use when running concurrent routines. If numelems*(1+refine)>=threshold then 0 is
    returned to indicate the number of processes used will be however many processes exist (usually the number of
    physical cores). A value of 1 is returned otherwise to clue in the system to compute sequentially in the calling
    process. If `numelems' is less than the number of processes used, only that many will actually be used.
    '''
    realprocs=ProcessServer.globalServer.realnumprocs

    try:
        numelemadjust=int(numelems*(refine+1))
    except:
        numelemadjust=int(numelems)

    if numelemadjust<threshold: # compute sequentially
        return 1
    elif numelems<=realprocs: # compute with `numelems' processors
        return numelems
    else: # compute with all processors
        return 0


def checkResultMap(result):
    '''Checks the results from a concurrent operation, throwing the first returned exception.'''
    for i in sorted(result):
        if isinstance(result[i],Exception):
            raise result[i]
            

def sumResultMap(result):
    '''Given a result dict mapping process indices to lists, returns the summed lists in order.'''
    return listSum(result[i] for i in sorted(result))


def listResults(result):
    '''Returns a list of the results from the given result map in process order.'''
    return [result[i] for i in sorted(result)]
    

def concurrent(func):
    '''
    Replaces `func' with a wrapper which will call `func' in parallel using processes. The first argument of `func'
    must be the AlgorithmProcess instance corresponding to the subprocess it is being called in. When calling the
    decorated form of `func' the first three arguments provided must be the `valrange', `numprocs', and `task' values
    expected by ProcessServer.callProcessFunc(), followed by the arguments normally passed to `func'.
    
    Applying this decorator creates a global variable with the name '__local__'+func.__name__ referencing a lambda
    function which calls `func' when evaluated. This is passed to 'ProcessServer.globalServer.callProcessFunc' when the
    call is made and is needed for picklability. Do not call this created function directly. This is necessary since 
    pickling a function only wraps up its name in the output which is looked up in the global namespace when unpickled.
    
    This decorator can only be applied to a function declared in a module which is loaded by each process when the app
    starts up. Since functions are looked up in the global namespace, any functions declared after module import time
    are not present in the processes global namespace and thus cannot be called. 
    
    Example (assuming this declared in a module):
        @concurrent 
        def testfunc(process,values):
            return (process.index,values)
        
        values=range(10)
        result=testfunc(len(values),3,None,values,partitionArgs=(values,))
        printFlush(listResults(result))
        
    Output: [(0, [0, 1, 2]), (1, [3, 4, 5]), (2, [6, 7, 8, 9])]
    '''
    
    try:
        mcode=marshal.dumps(func.__code__)
        func=types.FunctionType(marshal.loads(mcode))
        isBuiltInfunc=False
    except:
        module=inspect.getmodule(func)
        modname=module.__name__ if module is not None else ''
        isBuiltInfunc=inspect.isbuiltin(func) or modname.startswith('eidolon.') or modname.startswith('plugins.')
    
    if isBuiltInfunc:
        localname='__local__'+func.__name__
        globals()[localname]=lambda *args,**kwargs:func(*args,**kwargs) # create a new function in the global scope
        globals()[localname].__name__=localname # rename that function so that it can be matched up when unpickled
        globals()[localname].__qualname__=localname # needed in Python 3
        
        @functools.wraps(func)
        def concurrent_wrapper(valrange,numprocs,task,*args,**kwargs):
            future=ProcessServer.globalServer.callProcessFunc(valrange,numprocs,task,globals()[localname],*args,**kwargs)
            return future(None)
    else:
        @functools.wraps(func)
        def concurrent_wrapper(valrange,numprocs,task,*args,**kwargs):
            future=ProcessServer.globalServer.callProcessFunc(valrange,numprocs,task,concurrentFuncExec,mcode,{},*args,**kwargs)
            return future(None)
            
    return concurrent_wrapper


def concurrentFuncExec(process,mcode,cglobals,*args,**kwargs):
    execglobals=dict(globals())
    
    execglobals.update(cglobals)
    codeobj=marshal.loads(mcode)
    func=types.FunctionType(codeobj,execglobals)
    return func(process,*args,**kwargs)
    

@concurrent
def concurrentExec(process,execcode,clocals={},cglobals={},returnName=None):
    '''
    Execute the code string `execcode' in separate processes using the `clocals' and `cglobals' environment dictionaries.
    If `returnName' is given this is queried first from `clocals', then `cglobals' if not found, and returned.
    '''
    execlocals=dict(clocals)
    execglobals=dict(globals())
    
    execlocals['process']=process
    execglobals.update(cglobals)
    
    exec(execcode,execlocals,execglobals)
    
    if returnName:
        return execlocals.get(returnName,execglobals[returnName])
    

### Routines used by unit tests, these have to be here to be defined in the module namespace

@concurrent
def concurrencyTestRange(process,values):
    '''
    For each range index value, appends the value from `values' to the result. At every process.total index, prints each 
    indexed value in `values' to stdout then syncs with other processes. Returns the process' index range of `values'.
    '''
    result=[]
    for i in process.prange():
        result.append(i)
            
        if (i-process.startval)%process.total==0:
            printFlush(process.index,i,values[i])
            process.sync()

    return result


def concurrencyTestProcessValues(process):
    '''Returns the index, PID, startval, and endval for the given `process' object.'''
    return (process.index,os.getpid(),int(process.startval),int(process.endval))
    

@concurrent 
def concurrencyTestReturnArg(process,values):
    '''Returns the `process' index and `values'.'''
    return (process.index,values)


@concurrent
def concurrencyTestShareObjects(process):
    '''Test sharing objects bween processes using ShareObject().'''
    printFlush('Index',process.index,'Shared Object:',process.shareObject('index',process.index))
