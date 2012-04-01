# PyAlgoTrade
# 
# Copyright 2011 Gabriel Martin Becedillas Ruiz
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#   http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
.. moduleauthor:: Gabriel Martin Becedillas Ruiz <gabriel.becedillas@gmail.com>
"""

import SimpleXMLRPCServer
import threading
import time
import pickle
import threading
import random
from pyalgotrade import optimizer

class AutoStopThread(threading.Thread):
	def __init__(self, server):
		threading.Thread.__init__(self)
		self.__server = server

	def run(self):
		while self.__server.jobsPending():
			time.sleep(1)
		self.__server.stop()

class Job:
	def __init__(self, strategyParameters):
		self.__strategyParameters = strategyParameters
		self.__result = None
		self.__id = id(self)

	def getId(self):
		return self.__id

	def getStrategyParameters(self):
		return self.__strategyParameters

	def getResult(self):
		return self.__result

	def setResult(self, result):
		self.__result = result

# Restrict to a particular path.
class RequestHandler(SimpleXMLRPCServer.SimpleXMLRPCRequestHandler):
    rpc_paths = ('/PyAlgoTradeRPC',)

class Server(SimpleXMLRPCServer.SimpleXMLRPCServer):
	def __init__(self, address, port, autoStop = True):
		SimpleXMLRPCServer.SimpleXMLRPCServer.__init__(self, (address, port), requestHandler=RequestHandler, logRequests=False, allow_none=True)

		self.__instrumentsAndBars = None # Pickle'd instruments and bars for faster retrieval.
		self.__activeJobs = {}
		self.__activeJobsLock = threading.Lock()
		self.__parametersLock = threading.Lock()
		self.__bestJob = None
		self.__parametersIterator = None
		self.__logger = optimizer.get_logger("server")
		if autoStop:
			self.__autoStopThread = AutoStopThread(self)
		else:
			self.__autoStopThread = None

		self.register_introspection_functions()
		self.register_function(self.getInstrumentsAndBars, 'getInstrumentsAndBars')
		self.register_function(self.getNextJob, 'getNextJob')
		self.register_function(self.pushJobResults, 'pushJobResults')
		self.__forcedStop = False

	def __getRandomActiveJob(self):
		ret = None
		with self.__activeJobsLock:
			if len(self.__activeJobs) > 0:
				ret = random.choice(self.__activeJobs.values())
		return ret

	def getLogger(self):
		return self.__logger

	def setLogger(self, logger):
		self.__logger = logger

	def getInstrumentsAndBars(self):
		return self.__instrumentsAndBars

	def getBestJob(self):
		return self.__bestJob

	def getNextJob(self):
		ret = None
		params = None

		# Get the next set of parameters.
		with self.__parametersLock:
			try:
				if self.__parametersIterator:
					params = self.__parametersIterator.next()
			except StopIteration:
				self.__parametersIterator = None

		# Map the active job
		if params != None:
			ret = Job(params)
			with self.__activeJobsLock:
				self.__activeJobs[ret.getId()] = ret

		# If there are no more parameters, try to resubmit any active job.
		# if ret == None:
		# 	ret = self.__getRandomActiveJob()

		return pickle.dumps(ret)

	def jobsPending(self):
		if self.__forcedStop:
			return False

		with self.__parametersLock:
			jobsPending = self.__parametersIterator != None
		with self.__activeJobsLock:
			activeJobs = len(self.__activeJobs) > 0
		return jobsPending or activeJobs

	def pushJobResults(self, jobId, result):
		jobId = pickle.loads(jobId)
		result = pickle.loads(result)

		job = None

		# Get the active job and remove the mapping.
		with self.__activeJobsLock:
			try:
				job = self.__activeJobs[jobId]
				del self.__activeJobs[jobId]
			except KeyError:
				# The job's results were already submitted.
				return

		# Save the job with the best result
		if self.__bestJob == None or result > self.__bestJob.getResult():
			job.setResult(result)
			self.__bestJob = job

		self.getLogger().info("Partial result $%.2f with parameters: %s" % (result, job.getStrategyParameters()))

	def stop(self):
		self.shutdown()

	def serve(self, barFeed, strategyParameters):
		try:
			# Initialize instruments, bars and parameters.
			self.getLogger().info("Loading bars")
			loadedBars = []
			for bars in barFeed:
				loadedBars.append(bars)
			instruments = barFeed.getRegisteredInstruments()
			self.__instrumentsAndBars = pickle.dumps((instruments, loadedBars))

			self.__parametersIterator = iter(strategyParameters)

			if self.__autoStopThread:
				self.__autoStopThread.start()

			self.getLogger().info("Waiting for workers")
			self.serve_forever()

			if self.__autoStopThread:
				self.__autoStopThread.join()

			# Show the best result.
			bestJob = self.getBestJob()
			if bestJob:
				self.getLogger().info("Best final result $%.2f with parameters: %s" % (bestJob.getResult(), bestJob.getStrategyParameters()))
			else:
				self.getLogger().error("No jobs processed")
		finally:
			self.__forcedStop = True

def serve(barFeed, strategyParameters, address, port):
	"""Executes a server that will provide bars and strategy parameters for workers to use.

	:param barFeed: The bar feed that each worker will use to backtest the strategy.
	:type barFeed: :class:`pyalgotrade.barfeed.BarFeed`.
	:param strategyParameters: The set of parameters to use for backtesting. An iterable object where each element is a tuple that holds parameter values.
	:param address: The address to listen for incoming worker connections.
	:type address: string.
	:param port: The port to listen for incoming worker connections.
	:type port: int.
	"""
	s = Server(address, port)
	s.serve(barFeed, strategyParameters)
