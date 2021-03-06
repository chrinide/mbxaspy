""" Definitions of classes for parallel computing """

from __future__ import print_function

import sys
import os

from utils import *
from constants import *


class pool_class(object):
    """ 
    pool class: define parallelization over k-points

    Each pool has a given number of procs (nproc_per_pool) and 
    takes care of ONE k-point of ONE spin at a time.

    """

    def __init__(self, para = None):
        self.para = para
        self.pool_list = []
        self.up = False
        self.msg = ''

    def set_pool(self, nproc_per_pool = 1, remainder_mode = False):
        """ 
        set up pools so that each pool has at least nproc_per_pool procs 
        
        examples:
        para.size       = 10    # total number of processors
        nproc_per_pool  = 3     # least number of processors per pool
        10 / 3 = 3 pools

        Remainder Mode:

        pool    size    proc
           0       4    0, 3, 6, 9   
           1       3    1, 4, 7
           2       3    2, 5, 8

        Contiguous Mode:

        pool    size    proc
           0       4    0, 1, 2, 3   
           1       3    4, 5, 6
           2       3    7, 8, 9

        """
        para = self.para
        comm = para.comm
        if nproc_per_pool > 0:
            self.npp    = int(nproc_per_pool)
            if para.size < self.npp:
                para.print(' Insufficient number of procs for nproc_per_pool = ' + str(self.npp))
                para.print(' Reduce nproc_per_pool to ' + str(para.size))
                self.npp = para.size
            self.n      = int(para.size / self.npp)  # number of pools
            # the index of the pool
            if remainder_mode:
                self.i = int(para.rank % self.n)
            else:
                res = int(para.size % self.npp)
                if para.rank < res * (self.npp + 1):
                    self.i = int(para.rank / (self.npp + 1))
                else:
                    self.i = int((para.rank - res * (self.npp + 1)) / self.npp) + res
            if comm:
                # set up pool communicators
                # define intrapool comm
                if remainder_mode:
                    # remainder mode
                    self.comm = comm.Split(para.rank % self.n, para.rank) 
                else:
                    # contiguous mode
                    self.comm = comm.Split(self.i, para.rank)
                # actual pool size (npp plus residue)
                self.size     = self.comm.Get_size()
                self.rank     = self.comm.Get_rank()      # rank within the pool
                # rootcomm
                ranks = comm.gather(self.rank, root = 0)
                ranks = comm.bcast(ranks, root = 0)
                self.roots    = [ir for ir, r in enumerate(ranks) if r == 0]             # a list of all the pool roots
                roots_group   = para.MPI.Group.Incl(comm.Get_group(), self.roots)
                self.rootcomm = comm.Create_group(roots_group) # communication among the roots of all pools
            else:
                self.comm       = None
                self.rank       = 0
                self.size       = 1
                # rootcomm
                self.rootcomm   = None
                self.roots      = [0]
            self.up = True
    
    def info(self):
        """ Collect pool information and print """
        para = self.para
        comm = para.comm
        if not self.pool_list:
            if comm and self.comm:
                mypool = self.comm.gather(para.rank, root = 0)
                if self.rootcomm != para.MPI.COMM_NULL:
                    self.pool_list = self.rootcomm.gather(mypool, root = 0)
                self.pool_list = comm.bcast(self.pool_list, root = 0)
            else:
                mypool = [0]
                self.pool_list = [mypool]
        para.print(' Setting up pools ...')
        para.print(' {0:<4}{1:>6}{2:<6}'.format('pool', 'size', '  proc'))
        for i in range(self.n):
            para.print(' {0:>4}{1:>6}{2:<6}'.format(i, len(self.pool_list[i]), '  ' + str(self.pool_list[i]).strip('[]')))
        para.print()
        
    def set_sk_list_v1(self, nspin = 1, nk = 1, nk_use = 1):
        """ 
        set up a list of spin and kpoint tuples to be processed on this proc

        example:
        nspin = 2, nk = 5
        self.n  =   3   # number of pools
        2 * 5 / 3 = 3   # each pool at least deal with 3 tuples

        pool    tuple                           offset
           0    (0, 0), (0, 3), (1, 1), (1, 4)  0, 3, 6, 9
           1    (0, 1), (0, 4), (1, 2)          1, 4, 7
           2    (0, 2), (1, 0), (1, 3)          2, 5, 8
        """
        self.nspin = nspin
        self.nk = nk
        self.nk_use = nk_use
        self.sk_list = []
        self.sk_offset = []
        for s in range(nspin):
            for k in range(nk_use):
                offset = s * nk + k
                if offset % self.n == self.i:
                    self.sk_list.append((s, k))
                    self.sk_offset.append(offset)
        self.nsk = len(self.sk_list)

    def set_sk_list(self, nspin = 1, nk = 1, nk_use = 1):
        """ 
        set up a list of spin and kpoint tuples to be processed on this proc

        example:
        nspin = 2, nk = 5
        self.n  =   3   # number of pools
        5 / 3 = 1   # each pool at least deal with 1 k-points

        pool    tuple                           offset
           0    (0, 0), (1, 0), (0, 1), (1, 1)  0, 5, 1, 6
           1    (0, 2), (1, 2), (0, 3), (1, 3)  2, 7, 3, 8
           2    (0, 4), (1, 4)                  4, 9
        """
        self.nspin = nspin
        self.nk = nk
        self.nk_use = nk_use
        self.sk_list = []
        self.sk_offset = []
        nk_per_pool = int(self.nk_use / self.n)
        resk = int(self.nk_use % self.n)
        start_k = nk_per_pool * self.i + min(resk, self.i)
        for k in range(start_k, start_k + nk_per_pool + int(self.i < resk)):
            for s in range(nspin):
                offset = s * nk + k
                self.sk_list.append((s, k))
                self.sk_offset.append(offset)
        self.nsk = len(self.sk_list)

    def sk_info(self):
        """ collect and print out the spin-kpoint tuples on each pool """

        para = self.para
        if self.rootcomm:
            self.sk_list_all = self.rootcomm.gather(self.sk_list, root = 0)
            self.sk_list_all = self.rootcomm.bcast(self.sk_list_all, root = 0)
        else:
            self.sk_list_all = [self.sk_list]
        self.sk_list_maxl = max([len(skl) for skl in self.sk_list_all])
        para.print("{0:>5}     {1:<}".format('pool', 'sk-tuple'))
        for i, skl in enumerate(self.sk_list_all):
            para.print("{0:>5}     {1:<}".format(i, str(skl).strip('[]')), flush = True)
        para.print()

    def isroot(self):
        """ is this the root of the current pool ? """
        if self.para.rank in self.roots: return True
        else: return False

    def print(self, msg, flush = False):
        """ print at pool root """
        if self.isroot():
            print(msg) 
            if flush: sys.stdout.flush()

    def log(self, msg = '', flush = False):
        """ log messages from each proc and print on demand """
        if len(msg) > 0:
            self.msg += msg + '\n'
        if flush:
            if self.rootcomm: 
                msgs = self.rootcomm.gather(self.msg, root = 0)
                if self.rootcomm.Get_rank() == 0:
                    for i, m in enumerate(msgs):
                        if len(m) > 0:
                            print('pool {0}:\n'.format(i) + m)
            else:
                print(self.msg)
            self.msg = ''
            sys.stdout.flush()


class para_class(object):
    """ para class: wrap up user-defined mpi variables for parallelization """


    def __init__(self, MPI = None):
        if MPI is not None:
            self.MPI  = MPI
            self.comm = MPI.COMM_WORLD
            self.size = self.comm.Get_size()
            self.rank = self.comm.Get_rank()

        else:
            self.MPI  = None
            self.comm = None
            self.size = 1
            self.rank = 0

        self.msg = '' # message recorded
        # initialize pool for k-points
        self.pool = pool_class(self)

    def isroot(self):
        """ check if this is the global root """
        if self.rank == 0: return True
        else: return False

    def print(self, msg = '', rank = 0, flush = False):
        """ print at given rank """
        if self.rank == rank:
            print(msg) # recursively ?
        if flush:
            sys.stdout.flush()

    def stop(self):
        """ stop the code """
        if self.comm is not None:
            self.MPI.Finalize()
        sys.exit(0)

    def exit(self):
        if self.comm is not None:
            self.comm.Abort(0)
        else:
            sys.exit(0)

    def done(self):
        self.print('mbxaspy done', flush = True)
        self.stop()

    def error(self, msg = ''):
        """ print error message and quit """
        self.print('Error: ' + msg + '\nHalt.', flush = True)
        self.stop()

    def log(self, msg = '', flush = False):
        """ log messages from each proc and print on demand """
        if len(msg) > 0:
            self.msg += msg + '\n'
        if flush:
            if self.comm: 
                msgs = self.comm.gather(self.msg, root = 0)
                if self.comm.Get_rank() == 0:
                    for i, m in enumerate(msgs):
                        if len(m) > 0:
                            print('proc {0}:\n'.format(i) + m)
            else:
                print(self.msg)
            sys.stdout.flush()
            self.msg = ''
   
    def sep_line(self, sep = main_sepl):
        """ separation line"""
        self.print(sep)


