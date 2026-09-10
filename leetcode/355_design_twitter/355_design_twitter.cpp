class Twitter {
public:
    int timestamp;
    map<int,vector<pair<int,int>>> tweets;
    map<int,unordered_set<int>> following;//unordered_set for to unfollow in o(1) operation i consider vector it would have taken o(n) operation to unfollow 
    Twitter() {
        timestamp=0;
    }
    
    void postTweet(int userId, int tweetId) {
        tweets[userId].push_back({timestamp,tweetId});
        timestamp++;
    }
    
    vector<int> getNewsFeed(int userId) {
        priority_queue<vector<int>> pq;
        if (following.find(userId) != following.end()) {//user has followed someone then only we will traverse 
            for(auto &followee :following[userId]){
                if (!tweets[followee].empty()) {
                    int last_tweet_ind = tweets[followee].size() - 1;
                    int last_tweet_time = tweets[followee][last_tweet_ind].first;
                    int tweet_id = tweets[followee][last_tweet_ind].second;
                    pq.push({last_tweet_time, tweet_id, followee, last_tweet_ind});
                }
            }
        }    
       // Check if the list is NOT empty before trying to access size() - 1
        if (!tweets[userId].empty()) {
            int lastIndex = tweets[userId].size() - 1;
            pq.push({
                tweets[userId][lastIndex].first, 
                tweets[userId][lastIndex].second, 
                userId, 
                lastIndex
            });
        }

        
        vector<int> newsfeed;
        while(!pq.empty() && newsfeed.size()<10){
            vector<int> post=pq.top();
            pq.pop();
            int tweet=post[1];
            int ind=post[3];
            int user=post[2];

            if(ind>0){
                int nxt_ind=ind-1;

                int time=tweets[user][nxt_ind].first;

                int tweet_id=tweets[user][nxt_ind].second;

                pq.push({time,tweet_id,user,nxt_ind});

            }
            

            newsfeed.push_back(tweet);
            
        }
        return newsfeed;
    }
    
    void follow(int followerId, int followeeId) {
        following[followerId].insert(followeeId);
    }
    
    void unfollow(int followerId, int followeeId) {
        if(following[followerId].find(followeeId)!=following[followerId].end())
        {
            following[followerId].erase(followeeId);
        }
        
    }
};

/**
 * Your Twitter object will be instantiated and called as such:
 * Twitter* obj = new Twitter();
 * obj->postTweet(userId,tweetId);
 * vector<int> param_2 = obj->getNewsFeed(userId);
 * obj->follow(followerId,followeeId);
 * obj->unfollow(followerId,followeeId);
 */
