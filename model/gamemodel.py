import random
import os
import sys
import socket
import select
import time

from tools import algos
from tools import parser as pparser
from tools import communication as ccommunication
from tools import situation as ssituation
from tools import tools
from tools import think

import tensorflow as tf
import random
import numpy as np
from collections import deque

from .base import Model
from ops import *
from ops_network import *

debug = tools.debug
FINAL_EPSILON = 0.05 # final value of epsilon
INITIAL_EPSILON = 1.0 # starting value of epsilon
ACTIONS = 7 # number of valid actions
OBSERVE = 50.0 # timesteps to observe before training
EXPLORE = 10000.0 # frames over which to anneal epsilon
GAMMA = 0.99 # decay rate of past observations
REPLAY_MEMORY = 9000 # number of previous transitions to remember
BATCH = 10 # size of minibatch
NB_CHUNK = 25
size = 10 #split(size)

class GameModel(Model):
	"""Deep Game Network."""
	def __init__(self, sess,server_name, server_port):
		self.sess = sess
		self.build_model()
		self.init_server(server_name,server_port)
		
	def init_server(self,server_name,server_port):
		server_name = server_name
		server_port = int(server_port)

		# Connect to the server.
		server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			server.connect((server_name, server_port))
		except:
			print "Unable to connect"
			sys.exit(1)

		server_fd = server.makefile()

		self.situation = ssituation.Situation()
		self.parser = pparser.Parser(self.situation,[])
		self.communication = ccommunication.Communication(self.parser, server, server_fd)

	def build_model(self):
		# network weights
		W_conv1 = weight_variable([4, 4, 4, 16])
		b_conv1 = bias_variable([16])
		
		W_conv2 = weight_variable([3, 3, 16, 16])
		b_conv2 = bias_variable([16])
		
		W_fc2 = weight_variable([144, ACTIONS])
		b_fc2 = bias_variable([ACTIONS])
		
		W_fc1 = weight_variable([144, 144])
		b_fc1 = bias_variable([144])
		
		# input layer
		self.input_layer = tf.placeholder("float", [None, 10, 10, 4])

		# hidden layers
		h_conv1 = tf.nn.relu(conv2d(self.input_layer, W_conv1, 2) + b_conv1)
		h_pool1 = max_pool_2x2(h_conv1)
		
		h_conv2 = tf.nn.relu(conv2d(h_pool1, W_conv2, 1) + b_conv2)
		
		h_conv3_flat = tf.reshape(h_conv2, [-1, 144])
		
		self.h_fc1 = tf.nn.relu(tf.matmul(h_conv3_flat, W_fc1) + b_fc1)
		
		# readout layer
		self.readout = tf.matmul(self.h_fc1, W_fc2) + b_fc2
		
	def play_random_action(self, piece_id):
		piece = self.situation.player_pieces[piece_id]
		loc = self.situation.get_player_piece_location(piece_id)
		depth = self.situation.pieces_types[piece.piece_type].speed
		def crossable(a):
			qa, ra = a
			return self.situation.is_in_map((qa, ra)) and \
					self.situation.can_player_piece_be_on(piece_id, (qa, ra)) and \
					self.situation.is_tile_none((qa, ra))
		def neighbors(a):
			qa, ra = a
			result = []
			for direction in algos.directions:
				qd, rd = direction
				qb, rb = qa + qd, ra + rd
				if self.situation.is_in_map((qb, rb)) and self.situation.can_player_piece_be_on(piece_id, (qb, rb)):
					result.append( (qb, rb) )
			return result
		def cost(x, y):
			return 1
		heuristic = self.situation.get_tiles_distance
		destinations, came_from = algos.breadth_first_search_all(loc, depth, neighbors, cost, heuristic, crossable)
		if len(destinations) > 0:
			dest = random.choice(destinations)
			self.communication.action("moves %d %d %d" % (piece_id, dest[0], dest[1]))
	
	def play(self, learning_rate=0.001,
            checkpoint_dir="checkpoint",load=0):
		"""
		Args:
		  learning_rate: float, The learning rate [0.001]
		  checkpoint_dir: str, The path for checkpoints to be saved [checkpoint]
		"""
		self.learning_rate = learning_rate
		self.checkpoint_dir = checkpoint_dir

		self.step = tf.Variable(0, trainable=False)
		
		sess = tf.Session()
		
		one = tf.constant(1)
		new_value = tf.add(self.step, one)
		update = tf.assign(self.step, new_value)
		
		a = tf.placeholder("float", [None, ACTIONS])
		y = tf.placeholder("float", [None])
		
		readout_action = tf.reduce_sum(tf.mul(self.readout, a), reduction_indices = 1)
		cost = tf.reduce_mean(tf.square(y - readout_action))
		train_step = tf.train.AdamOptimizer(learning_rate).minimize(cost)		
		
		# store the previous observations in replay memory
    		D = deque()
    
    		# get the first state by doing nothing and preprocess the image to 80x80x4
		do_nothing = np.zeros(ACTIONS)
	
		# initialize all variables
		tf.initialize_all_variables().run()
		
		sess.run(self.step.assign(0))
		
		# loading networks
		if load :
			self.load(checkpoint_dir)
		
		start_time = time.time()

		t = 0
		epsilon = INITIAL_EPSILON
		
		# Static allocation to be safe
		readout_t = [None]*NB_CHUNK
		s_t = [None]*NB_CHUNK
		s_t1 = [None]*NB_CHUNK
		a_t = [[None]*ACTIONS]*NB_CHUNK # vector of vector which contain the executed action
		r_t = [None]*NB_CHUNK
		
		
		while 1:
			self.communication.wait()

			#debug("nb cities: %d" % len(self.situation.player_cities))
			#debug("nb pieces: %d" % len(self.situation.player_pieces))

			self.situation.check()
			chunks = self.situation.split(10)
			for city_id in self.situation.player_cities:
				think.choose_relevant_random_production(self.situation, self.communication, city_id)
			
				
			for i in range(len(chunks)):
				chunk = chunks[i]
				
				for q in range(len(chunk)):
					for r in range(len(chunk[q])):
						if chunk[q][r].visible == False:
							chunk[q][r] = 0
						else:
							if chunk[q][r].content == None:
								if chunk[q][r].terrain == ssituation.Situation.GROUND:
									chunk[q][r] = 1
								else:
									chunk[q][r] = 2
							else:
								if isinstance(chunk[q][r].content, ssituation.City):
									chunk[q][r] = 3
								elif isinstance(chunk[q][r].content, ssituation.OwnedCity):
									chunk[q][r] = 4 + chunk[q][r].content.owner # 4 et 5 !!!
								else:
									chunk[q][r] = 6 + chunk[q][r].content.piece_type_id + chunk[q][r].content.owner * len(self.situation.piece_types)
				#print chunk
				s_t[i] = chunk
				s_t[i]  = np.stack((s_t[i] , s_t[i] , s_t[i] ,s_t[i] ), axis = 2) #check for last four
				#evaluate with current model 
				readout_t[i] = self.readout.eval(feed_dict = {self.input_layer : [s_t[i]]})[0]
				a_t[i] = np.zeros([ACTIONS],dtype=float)
			
			# scale down epsilon
			if epsilon > FINAL_EPSILON:
				epsilon -= (INITIAL_EPSILON - FINAL_EPSILON) / EXPLORE
		
		
			# play actions
			piece_ids = self.situation.player_pieces.keys()
			if t % 20 == 0:
				print("Nb piece : {}".format(len(piece_ids)))
			for piece_id in piece_ids:
				#while can go further
				piece = self.situation.player_pieces[piece_id]
				depth = self.situation.piece_types[piece.piece_type_id].speed
				#find good chunk
				i = self.situation.split_int(size,piece_id)
				while depth > 0 and self.situation.is_player_piece(piece_id):
					# check in the vector the best choice
					directions = algos.directions
					if not self.situation.is_player_piece(piece_id):
						break
					loc = piece.get_location()
					result = []
					for dir in range(len(directions)):
						next_location = loc[0] + directions[dir][0], loc[1] + directions[dir][1] # x , y
						if self.situation.can_player_piece_be_on(piece_id, next_location):
							# keep coef and check the content of the tile
							# next_location is next(x,y)
							result.append(dir)
							if self.situation.is_in_map(next_location):
								if self.situation.is_tile_none(next_location):
									if self.situation.get_terrain(next_location) == ssituation.Situation.GROUND or self.situation.get_terrain(next_location) == ssituation.Situation.WATER or self.situation.is_tile_player_city(next_location):
										readout_t[i][dir]=float(readout_t[i][dir])*0.5
								else:
									if self.situation.is_tile_free_city(next_location) or self.situation.is_tile_enemy_city(next_location):
										readout_t[i][dir]= 1 # we take it
									elif self.situation.is_tile_enemy_piece(next_location):
										readout_t[i][dir]=float(readout_t[i][dir])*0.3 # TODO update with kind of troops
									elif self.situation.is_tile_player_piece(next_location) :
										readout_t[i][dir]=0
									else:
										assert("Should not pass here.");
						else:
							# can't play this directions
							readout_t[i][dir] = 0
					if len(result)>0:		
						if random.random() <= epsilon or t <= OBSERVE:
							direction = random.choice(result) # choose the one gave by the output_vector x ProbaVector
							self.communication.action("move %d %d" % (piece_id, direction))
						else:
						    	action_index = np.argmax(readout_t[i]) #this gets only the best action_index
						    	if action_index != 6:
							    	self.communication.action("move %d %d" % (piece_id, action_index))
					depth = depth - 1
					
				## Observe the action and evaluate the result (Q function)
					# check only if alive				
				if not self.situation.is_player_piece(piece_id):
					r_t[i] = -1
				else:
					r_t[i] = 1

				self.situation.check()
				chunks = self.situation.split(10)
				# TODO : check if game ended
				terminal = 0
				
				#state result
				for j in range(len(chunks)):
					s_t1[j] = s_t[j]
				
				# store the transition in D
				D.append((s_t[i], a_t[i], r_t[i], s_t1[i], terminal))

			if t> OBSERVE:
				# sample a minibatch to train on
				minibatch = random.sample(D, BATCH)
				# get the batch variables
				s_j_batch = [d[0] for d in minibatch]
				a_batch = [d[1] for d in minibatch]
				r_batch = [d[2] for d in minibatch]
				s_j1_batch = [d[3] for d in minibatch]
				
				y_batch = []
				readout_j1_batch = self.readout.eval(feed_dict = {self.input_layer : s_j1_batch})
				for i in range(0, len(minibatch)):
				# if terminal only equals reward
					if minibatch[i][4]:
						y_batch.append(r_batch[i])
					else:
						y_batch.append(r_batch[i] + GAMMA * float(np.max(readout_j1_batch[i])))
						
				train_step.run(feed_dict = {
					y : y_batch,
					a : a_batch,
					self.input_layer : s_j_batch})
			self.communication.end_turn()
			# update the old values
			for i in range(len(chunks)):
				s_t[i] = s_t1[i]
			t += 1
			# Save checkpoint each 300 steps
			if t != 0 and t % 100 == 0:
				self.save(checkpoint_dir, step)
			# Show current progress
			step = sess.run(self.step)
			if t % 100 == 1:
				print("Epoch: [%2d] time: %4.4f, epsilon: %.8f" % (t, time.time() - start_time, epsilon))
			
