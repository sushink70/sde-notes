class Solution {
public:
    vector<int> getOrder(vector<vector<int>>& tasks) {
        priority_queue<pair<int,int>,vector<pair<int,int>>,greater<pair<int,int>>> pq;
        int n=tasks.size();
      
        vector<vector<int>> t;
        for(int i=0;i<n; i++){
            t.push_back({tasks[i][0],tasks[i][1],i});
        }
        sort(t.begin(),t.end());
        int ct=t[0][0];
        vector<int> ans;
        int i=0;

        //intillay i was pushing 
        while(i<n ){
         
            while(i<n && t[i][0]<=ct){
                pq.push({t[i][1],t[i][2]});
                i++;//to push all elements at once those who arrived at same time [26,5] [59,4][59,12][59,11] [59,16]
            }
            if(pq.empty()){
                ct=t[i][0]; 
                continue;// if [26,5 ] , [52,33], [52,21] after first task processed time 26+5 =31 ct=31 but next task arrives at 59 cpu is idle for 28s therefore my current time should be directly 59s so irectly assigning current time to next arrival time ,
            } 
            int pt=pq.top().first;//getting minimum processing timw element at first 
            ans.push_back(pq.top().second);
            pq.pop();
            ct+=pt;
        }
        while(!pq.empty()){
            ans.push_back(pq.top().second);
            pq.pop();
        }
        return ans;
    }
};
