"""
.. module:: nested
   :synopsis: Nested sampler
.. moduleauthor:: Adam Moss <adam.moss@nottingham.ac.uk>
Performs nested sampling to calculate the Bayesian evidence and posterior samples
Some parts are from the Nestle library by Kyle Barbary (https://github.com/kbarbary/nestle)
"""

from __future__ import print_function
from __future__ import division

import os
import csv
import json
import glob
import numpy as np

from nnest.sampler import Sampler
from nnest.priors import UniformPrior


class NestedSampler(Sampler):

    def __init__(self,
                 x_dim,
                 loglike,
                 transform=None,
                 append_run_num=True,
                 run_num=None,
                 hidden_dim=16,
                 num_slow=0,
                 num_derived=0,
                 batch_size=100,
                 flow='spline',
                 num_blocks=3,
                 num_layers=1,
                 log_dir='logs/test',
                 use_gpu=False,
                 base_dist=None,
                 scale='',
                 num_live_points=1000
                 ):
        """

        Args:
            x_dim:
            loglike:
            transform:
            append_run_num:
            run_num:
            hidden_dim:
            num_slow:
            num_derived:
            batch_size:
            flow:
            num_blocks:
            num_layers:
            log_dir:
            use_gpu:
            base_dist:
            scale:
            num_live_points:
        """

        prior = UniformPrior(x_dim, -1, 1)

        super(NestedSampler, self).__init__(x_dim, loglike, transform=transform, append_run_num=append_run_num,
                                            run_num=run_num, hidden_dim=hidden_dim, num_slow=num_slow,
                                            num_derived=num_derived, batch_size=batch_size, flow=flow,
                                            num_blocks=num_blocks, num_layers=num_layers, log_dir=log_dir,
                                            use_gpu=use_gpu, base_dist=base_dist, scale=scale, prior=prior,
                                            transform_prior=False)

        self.num_live_points = num_live_points
        self.sampler = 'nested'

        if self.log:
            self.logger.info('Num live points [%d]' % self.num_live_points)

        if self.log:
            with open(os.path.join(self.logs['results'], 'results.csv'), 'w') as f:
                writer = csv.writer(f)
                writer.writerow(['step', 'acceptance', 'min_ess',
                                 'max_ess', 'jump_distance', 'scale', 'loglstar', 'logz', 'fraction_remain', 'ncall'])

    def run(
            self,
            method='rejection_prior',
            mcmc_steps=0,
            mcmc_burn_in=0,
            mcmc_num_chains=10,
            max_iters=1000000,
            update_interval=None,
            log_interval=None,
            dlogz=0.5,
            train_iters=500,
            volume_switch=-1.0,
            step_size=0.0,
            jitter=-1.0,
            num_test_mcmc_samples=0,
            test_mcmc_steps=1000,
            test_mcmc_burn_in=0):
        """

        Args:
            method:
            mcmc_steps:
            mcmc_burn_in:
            mcmc_num_chains:
            max_iters:
            update_interval:
            log_interval:
            dlogz:
            train_iters:
            volume_switch:
            step_size:
            jitter:
            num_test_mcmc_samples:
            test_mcmc_steps:
            test_mcmc_burn_in:

        Returns:

        """

        if update_interval is None:
            update_interval = max(1, round(self.num_live_points))
        else:
            update_interval = round(update_interval)
            if update_interval < 1:
                raise ValueError("update_interval must be >= 1")

        if log_interval is None:
            log_interval = max(1, round(0.2 * self.num_live_points))
        else:
            log_interval = round(log_interval)
            if log_interval < 1:
                raise ValueError("log_interval must be >= 1")

        if mcmc_steps <= 0:
            mcmc_steps = 5 * self.x_dim

        if step_size == 0.0:
            step_size = 2 / self.x_dim ** 0.5

        if self.log:
            self.logger.info('MCMC steps [%d]' % mcmc_steps)
            self.logger.info('Initial scale [%5.4f]' % step_size)
            self.logger.info('Volume switch [%5.4f]' % volume_switch)

        it = 0
        if self.resume and not self.logs['created']:
            for f in glob.glob(os.path.join(self.logs['checkpoint'], 'checkpoint_*.txt')):
                if int(f.split('/checkpoint_')[1].split('.txt')[0]) > it:
                    it = int(f.split('/checkpoint_')[1].split('.txt')[0])

        if it > 0:

            self.logger.info('Using checkpoint [%d]' % it)

            with open(os.path.join(self.logs['checkpoint'], 'checkpoint_%s.txt' % it), 'r') as f:
                data = json.load(f)
                logz = data['logz']
                h = data['h']
                logvol = data['logvol']
                ncall = data['ncall']
                fraction_remain = data['fraction_remain']
                method = data['method']

            active_u = np.load(os.path.join(self.logs['checkpoint'], 'active_u_%s.npy' % it))
            active_v = self.transform(active_u)
            active_logl = np.load(os.path.join(self.logs['checkpoint'], 'active_logl_%s.npy' % it))
            active_derived = np.load(os.path.join(self.logs['checkpoint'], 'active_derived_%s.npy' % it))
            saved_v = np.load(os.path.join(self.logs['checkpoint'], 'saved_v.npy')).tolist()
            saved_logl = np.load(os.path.join(self.logs['checkpoint'], 'saved_logl.npy')).tolist()
            saved_logwt = np.load(os.path.join(self.logs['checkpoint'], 'saved_logwt.npy')).tolist()

        else:

            if self.use_mpi:
                self.logger.info('Using MPI with rank [%d]' % (self.mpi_rank))
                if self.mpi_rank == 0:
                    active_u = 2 * (np.random.uniform(size=(self.num_live_points, self.x_dim)) - 0.5)
                else:
                    active_u = np.empty((self.num_live_points, self.x_dim), dtype=np.float64)
                self.comm.Bcast(active_u, root=0)
            else:
                active_u = 2 * (np.random.uniform(size=(self.num_live_points, self.x_dim)) - 0.5)
            active_v = self.transform(active_u)

            if self.use_mpi:
                if self.mpi_rank == 0:
                    chunks = [[] for _ in range(self.mpi_size)]
                    for i, chunk in enumerate(active_u):
                        chunks[i % self.mpi_size].append(chunk)
                else:
                    chunks = None
                data = self.comm.scatter(chunks, root=0)
                active_logl = self.loglike(data)
                recv_active_logl = self.comm.gather(active_logl, root=0)
                recv_active_logl = self.comm.bcast(recv_active_logl, root=0)
                active_logl = np.concatenate(recv_active_logl, axis=0)
            else:
                active_logl, active_derived = self.loglike(active_u)

            saved_v = []  # Stored points for posterior results
            saved_logl = []
            saved_logwt = []

            h = 0.0  # Information, initially 0.
            logz = -1e300  # ln(Evidence Z), initially Z=0
            logvol = np.log(1.0 - np.exp(-1.0 / self.num_live_points))
            fraction_remain = 1.0
            ncall = self.num_live_points  # number of calls we already made

        first_time = True
        nb = self.mpi_size * mcmc_num_chains
        rejection_sample = volume_switch < 1
        ncs = []

        for it in range(it, max_iters):

            # Worst object in collection and its weight (= volume * likelihood)
            worst = np.argmin(active_logl)
            logwt = logvol + active_logl[worst]

            # Update evidence Z and information h.
            logz_new = np.logaddexp(logz, logwt)
            h = (np.exp(logwt - logz_new) * active_logl[worst] + np.exp(logz - logz_new) * (h + logz) - logz_new)
            logz = logz_new

            # Add worst object to samples.

            if self.num_derived > 0:
                saved_v.append(np.concatenate((active_v[worst], active_derived[worst])))
            else:
                saved_v.append(np.array(active_v[worst], copy=True))
            saved_logwt.append(logwt)
            saved_logl.append(active_logl[worst])

            expected_vol = np.exp(-it / self.num_live_points)

            # The new likelihood constraint is that of the worst object.
            loglstar = active_logl[worst]

            # Train flow
            if (method == 'mcmc' or method == 'rejection_flow') and (first_time or it % update_interval == 0):

                self.trainer.train(active_u, max_iters=train_iters, jitter=jitter)

                if num_test_mcmc_samples > 0:
                    # Test multiple chains from worst point to check mixing
                    init_x = np.concatenate(
                        [active_u[worst:worst + 1, :] for i in range(num_test_mcmc_samples)])
                    test_samples, _, _, _, _, scale, _ = self._mcmc_sample(
                        init_x=init_x, loglstar=loglstar, mcmc_steps=test_mcmc_steps + test_mcmc_burn_in,
                        step_size=step_size, dynamic_step_size=True)
                    np.save(os.path.join(self.logs['chains'], 'test_samples.npy'), test_samples)
                    self._chain_stats(test_samples, mean=np.mean(active_u, axis=0), std=np.std(active_u, axis=0))
                first_time = False

            if method == 'rejection_prior' or method == 'rejection_flow':

                # Rejection sampling

                nc = 0

                if method == 'rejection_prior':

                    # Simple rejection sampling over prior
                    while True:
                        u = 2 * (np.random.uniform(size=(1, self.x_dim)) - 0.5)
                        logl, der = self.loglike(u)
                        nc += 1
                        if logl > loglstar:
                            break

                else:

                    # Rejection sampling using flow
                    u, logl, nc = self._rejection_sample(loglstar, init_x=active_u)

                ncs.append(nc)
                mean_calls = np.mean(ncs[-10:])

                if self.use_mpi:
                    recv_samples = self.comm.gather(u, root=0)
                    recv_likes = self.comm.gather(logl, root=0)
                    recv_nc = self.comm.gather(nc, root=0)
                    recv_samples = self.comm.bcast(recv_samples, root=0)
                    recv_likes = self.comm.bcast(recv_likes, root=0)
                    recv_nc = self.comm.bcast(recv_nc, root=0)
                    samples = np.concatenate(recv_samples, axis=0)
                    likes = np.concatenate(recv_likes, axis=0)
                    ncall += sum(recv_nc)
                else:
                    samples = np.array(u)
                    derived_samples = np.array(der)
                    likes = np.array(logl)
                    ncall += nc
                for ib in range(0, self.mpi_size):
                    active_u[worst] = samples[ib, :]
                    active_v[worst] = self.transform(active_u[worst])
                    active_logl[worst] = likes[ib]
                    if self.num_derived > 0:
                        active_derived[worst] = derived_samples[ib, :]

                if it % log_interval == 0 and self.log:
                    self.logger.info(
                        'Step [%d] loglstar [%5.4e] max logl [%5.4e] logz [%5.4e] vol [%6.5e] ncalls [%d] mean '
                        'calls [%5.4f]' % (it, loglstar, np.max(active_logl), logz, expected_vol, ncall, mean_calls))

                if expected_vol < volume_switch >= 0 or (volume_switch < 0 and mean_calls > mcmc_steps):
                    # Switch to MCMC if rejection sampling becomes inefficient
                    self.logger.info('Switching to MCMC sampling')
                    method = 'mcmc'

            elif method == 'mcmc':

                # MCMC sampling

                accept = False
                while not accept:
                    if nb == self.mpi_size * mcmc_num_chains:
                        # Get a new batch of trial points
                        nb = 0
                        idx = np.random.randint(
                            low=0, high=self.num_live_points, size=mcmc_num_chains)
                        init_x = active_u[idx, :]
                        samples, latent_samples, derived_samples, likes, scale, nc = self._mcmc_sample(
                            init_x=init_x, loglstar=loglstar, mcmc_steps=mcmc_steps + mcmc_burn_in,
                            step_size=step_size, dynamic_step_size=True)
                        if self.use_mpi:
                            recv_samples = self.comm.gather(samples, root=0)
                            recv_likes = self.comm.gather(likes, root=0)
                            recv_nc = self.comm.gather(nc, root=0)
                            recv_samples = self.comm.bcast(recv_samples, root=0)
                            recv_likes = self.comm.bcast(recv_likes, root=0)
                            recv_nc = self.comm.bcast(recv_nc, root=0)
                            samples = np.concatenate(recv_samples, axis=0)
                            likes = np.concatenate(recv_likes, axis=0)
                            ncall += sum(recv_nc)
                        else:
                            ncall += nc
                    for ib in range(nb, self.mpi_size * mcmc_num_chains):
                        nb += 1
                        if np.all(samples[ib, 0, :] != samples[ib, -1, :]) and likes[ib, -1] > loglstar:
                            active_u[worst] = samples[ib, -1, :]
                            active_v[worst] = self.transform(active_u[worst])
                            active_logl[worst] = likes[ib, -1]
                            if self.num_derived > 0:
                                active_derived[worst] = derived_samples[ib, -1, :]
                            accept = True
                            break

                if it % log_interval == 0 and self.log:
                    acceptance, ess, jump_distance = self._chain_stats(samples, mean=np.mean(active_u, axis=0),
                                                                       std=np.std(active_u, axis=0))
                    self.logger.info(
                        'Step [%d] loglstar [%5.4e] maxlogl [%5.4e] logz [%5.4e] vol [%6.5e] ncalls [%d] '
                        'scale [%5.4f]' % (it, loglstar, np.max(active_logl), logz, expected_vol, ncall, scale))
                    with open(os.path.join(self.logs['results'], 'results.csv'), 'a') as f:
                        writer = csv.writer(f)
                        writer.writerow([it, acceptance, np.min(ess), np.max(ess),
                                         jump_distance, scale, loglstar, logz, fraction_remain, ncall])

            # Shrink interval
            logvol -= 1.0 / self.num_live_points
            logz_remain = np.max(active_logl) - it / self.num_live_points
            fraction_remain = np.logaddexp(logz, logz_remain) - logz

            if self.log:
                self.trainer.writer.add_scalar('logz', logz, it)

            self.samples = np.array(saved_v)
            self.weights = np.exp(np.array(saved_logwt) - logz)
            self.loglikes = -np.array(saved_logl)

            if it % log_interval == 0 and self.log:
                np.save(os.path.join(self.logs['checkpoint'], 'active_u_%s.npy' % it), active_u)
                np.save(os.path.join(self.logs['checkpoint'], 'active_logl_%s.npy' % it), active_logl)
                np.save(os.path.join(self.logs['checkpoint'], 'active_derived_%s.npy' % it), active_derived)
                np.save(os.path.join(self.logs['checkpoint'], 'saved_v.npy'), saved_v)
                np.save(os.path.join(self.logs['checkpoint'], 'saved_logl.npy'), saved_logl)
                np.save(os.path.join(self.logs['checkpoint'], 'saved_logwt.npy'), saved_logwt)
                with open(os.path.join(self.logs['checkpoint'], 'checkpoint_%s.txt' % it), 'w') as f:
                    json.dump({'logz': logz, 'h': h, 'logvol': logvol, 'ncall': ncall,
                               'fraction_remain': fraction_remain, 'method': method}, f)
                self._save_samples(self.samples, self.loglikes, weights=self.weights)

            # Stopping criterion
            if fraction_remain < dlogz:
                break

        logvol = -len(saved_v) / self.num_live_points - np.log(self.num_live_points)
        for i in range(self.num_live_points):
            logwt = logvol + active_logl[i]
            logz_new = np.logaddexp(logz, logwt)
            h = (np.exp(logwt - logz_new) * active_logl[i] + np.exp(logz - logz_new) * (h + logz) - logz_new)
            logz = logz_new
            saved_v.append(np.array(active_v[i]))
            saved_logwt.append(logwt)
            saved_logl.append(active_logl[i])

        self.logz = logz
        self.samples = np.array(saved_v)
        self.weights = np.exp(np.array(saved_logwt) - logz)
        self.loglikes = -np.array(saved_logl)

        if self.log:
            with open(os.path.join(self.logs['results'], 'final.csv'), 'w') as f:
                writer = csv.writer(f)
                writer.writerow(['niter', 'ncall', 'logz', 'logzerr', 'h'])
                writer.writerow([it + 1, ncall, logz, np.sqrt(h / self.num_live_points), h])
            self._save_samples(self.samples, self.loglikes, weights=self.weights)

        print("niter: {:d}\n ncall: {:d}\n nsamples: {:d}\n logz: {:6.3f} +/- {:6.3f}\n h: {:6.3f}"
              .format(it + 1, ncall, len(np.array(saved_v)), logz, np.sqrt(h / self.num_live_points), h))
